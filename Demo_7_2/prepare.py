"""Copy labeled recordings unchanged, profile fields, and prepare ML inputs.

Uses the Python standard library. It does not train a model or rank fields by
their association with the distance label. Raw files are never edited.
"""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import shutil


DEMO = Path(__file__).resolve().parent
DISTANCES = tuple(index / 2 for index in range(1, 8))
SIGNAL_FIELDS = ("rssi", "agc_gain", "fft_gain")
RADIO_FIELDS = ("mcs", "rate", "sig_mode", "bandwidth", "channel", "secondary_channel",
                "stbc", "sgi", "aggregation", "noise_floor", "antenna", "fec_coding")
LABEL_FIELDS = {"distance_m", "label_m", "label"}
GROUP_FIELDS = {"recording_id", "session_id", "orientation"}
BOOKKEEPING = {"packet_id", "request_id", "timestamp_us", "received_at_utc",
               "received_monotonic_ns", "elapsed_s", "recording_started_at_utc",
               "recording_duration_s", "schema_version", "type", "tx_mac", "rx_id"}
QUALITY_FIELDS = {"csi_len", "first_word_invalid", "rx_state", "gain_available", "csi_config"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_recordings(source, raw_dir):
    if source.resolve() == raw_dir.resolve():
        raise ValueError("Source and copied raw-data folder must differ.")
    files = sorted(source.glob("*.jsonl"))
    if not files:
        raise ValueError(f"No JSONL recordings found in {source}")
    raw_dir.mkdir(parents=True, exist_ok=True)
    for path in files:
        destination = raw_dir / path.name
        if destination.exists():
            if checksum(destination) != checksum(path):
                raise ValueError(f"Refusing to replace a different raw file: {destination}")
        else:
            shutil.copy2(path, destination)
        if checksum(destination) != checksum(path):
            raise OSError(f"Copy hash mismatch: {path.name}")


def valid_distance(value):
    return type(value) in (float, int) and value in DISTANCES


def packet_error(packet, distance):
    if not isinstance(packet, dict) or packet.get("type") != "capture":
        return "not a capture object"
    if not valid_distance(packet.get("distance_m")) or packet["distance_m"] != distance:
        return "packet/header distance mismatch or missing packet label"
    if packet.get("rx_state") != 0:
        return "missing or unsuccessful RX state"
    csi = packet.get("csi_raw")
    if not isinstance(csi, list) or not csi or len(csi) > 1024 or len(csi) % 2:
        return "CSI must be a nonempty, even-length byte array of at most 1024 bytes"
    if any(type(value) is not int or not -128 <= value <= 127 for value in csi):
        return "CSI byte outside signed int8 range"
    if type(packet.get("csi_len")) is not int or packet["csi_len"] != len(csi):
        return "CSI length does not match raw array"
    if type(packet.get("first_word_invalid")) is not bool:
        return "missing or invalid first-word flag"
    if not isinstance(packet.get("csi_config"), dict):
        return "missing CSI configuration"
    if not isinstance(packet.get("recording_id"), str) or not packet["recording_id"]:
        return "missing recording identity"
    if type(packet.get("packet_id")) is not int or not 0 <= packet["packet_id"] <= 0xFFFFFFFF:
        return "invalid packet identity"
    if type(packet.get("timestamp_us")) is not int or not 0 <= packet["timestamp_us"] <= 0xFFFFFFFF:
        return "invalid receive timestamp"
    for key, low, high in (("rssi", -128, 127), ("agc_gain", 0, 255), ("fft_gain", -128, 127)):
        if type(packet.get(key)) is not int or not low <= packet[key] <= high:
            return f"invalid or missing {key}"
    if packet.get("gain_available") is not True:
        return "gain metadata unavailable"
    elapsed = packet.get("elapsed_s")
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or not 0 <= elapsed < 10:
        return "packet outside the 10-second recording window"
    for key in ("sig_mode", "bandwidth", "stbc"):
        if type(packet.get(key)) is not int:
            return f"missing radio layout field: {key}"
    if packet["bandwidth"] not in (20, 40):
        return "unsupported bandwidth"
    return None


def load_packets(raw_dir):
    packets, rejected, recordings, manifest = [], [], [], []
    seen = {}
    recording_sources = {}
    for path in sorted(raw_dir.glob("*.jsonl")):
        manifest.append(dict(file=path.name, bytes=path.stat().st_size, sha256=checksum(path)))
        if path.name.endswith(".partial.jsonl"):
            rejected.append(dict(file=path.name, line=0, reason="partial recording excluded"))
            continue
        file_packets = []
        with path.open(encoding="utf-8-sig") as stream:
            try:
                header = json.loads(next(stream))
                if not isinstance(header, dict) or not valid_distance(header.get("distance_m")):
                    raise ValueError("first line must contain a valid distance_m")
            except (StopIteration, ValueError) as exc:
                rejected.append(dict(file=path.name, line=1, reason=f"invalid header: {exc}"))
                continue
            for line_number, line in enumerate(stream, 2):
                try:
                    packet = json.loads(line)
                    error = packet_error(packet, header["distance_m"])
                    if error:
                        raise ValueError(error)
                    identity = (packet["recording_id"], packet["packet_id"], packet["timestamp_us"])
                    encoded = canonical(packet)
                    if identity in seen:
                        reason = "duplicate packet" if seen[identity] == encoded else "conflicting packet identity"
                        raise ValueError(reason)
                    if packet["recording_id"] in recording_sources and recording_sources[packet["recording_id"]] != path.name:
                        raise ValueError("recording identity reused by another source file")
                    seen[identity] = encoded
                    recording_sources[packet["recording_id"]] = path.name
                    file_packets.append(dict(packet=packet, file=path.name, line=line_number))
                except ValueError as exc:
                    rejected.append(dict(file=path.name, line=line_number, reason=str(exc)))
        packets.extend(file_packets)
        recordings.append(dict(file=path.name, distance_m=header["distance_m"],
                               packets=len(file_packets),
                               recording_ids=sorted({p["packet"]["recording_id"] for p in file_packets}),
                               orientations=sorted({p["packet"].get("orientation", "") for p in file_packets})))
    return packets, rejected, recordings, manifest


def layout_key(packet):
    # Training-field offsets must not be combined across different CSI layouts.
    return canonical(dict(csi_len=packet["csi_len"], sig_mode=packet["sig_mode"],
                          bandwidth=packet["bandwidth"], stbc=packet["stbc"],
                          csi_config=packet["csi_config"]))


def prepare(raw_dir, output_dir):
    packets, rejected, recordings, manifest = load_packets(raw_dir)
    if not packets:
        raise ValueError("No valid labeled packets; raw files were retained unchanged.")
    observed = defaultdict(Counter)
    layouts = defaultdict(list)
    for entry in packets:
        packet = entry["packet"]
        for key, value in packet.items():
            if key != "csi_raw":
                observed[key][canonical(value)] += 1
        layouts[layout_key(packet)].append(packet)

    kept_scalars = [key for key in SIGNAL_FIELDS if len(observed[key]) > 1]
    context_fields = [key for key in RADIO_FIELDS if len(observed[key]) > 1]
    field_report = {}
    for key, counts in sorted(observed.items()):
        if key in LABEL_FIELDS:
            role, reason = "target", "distance label; never an input feature"
        elif key in GROUP_FIELDS:
            role, reason = "grouping", "retained for trial/session provenance, not model input"
        elif key in kept_scalars:
            role, reason = "feature", "varying measured signal/gain metadata"
        elif key in BOOKKEEPING:
            role, reason = "excluded", "bookkeeping/device identity; may encode collection order"
        elif key in context_fields:
            role, reason = "radio_context", "radio setting may affect CSI; not a default position feature"
        elif len(counts) == 1:
            role, reason = "excluded", "constant across valid packets in this dataset"
        elif key in QUALITY_FIELDS:
            role, reason = "quality_context", "validation/layout information, not position input"
        else:
            role, reason = "excluded", "unrecognized field; retained in raw source for review"
        field_report[key] = dict(role=role, reason=reason, present=sum(counts.values()),
                                 unique=len(counts), examples=[json.loads(value) for value, _ in counts.most_common(5)])

    layout_ids, layout_schema = {}, {}
    for index, (key, rows) in enumerate(sorted(layouts.items()), 1):
        layout_id = f"layout_{index}"
        layout_ids[key] = layout_id
        pair_count = len(rows[0]["csi_raw"]) // 2
        masked = {0, 1} if any(row["first_word_invalid"] for row in rows) else set()
        kept, constant, zero = [], [], []
        for offset in range(pair_count):
            values = {(row["csi_raw"][2 * offset], row["csi_raw"][2 * offset + 1]) for row in rows}
            if offset in masked:
                continue
            if len(values) == 1:
                constant.append(offset)
                if values == {(0, 0)}:
                    zero.append(offset)
            else:
                kept.append(offset)
        layout_schema[layout_id] = dict(layout=json.loads(key), packets=len(rows),
                                       original_pair_count=pair_count, kept_pair_offsets=kept,
                                       constant_pair_offsets=constant, all_zero_pair_offsets=zero,
                                       invalid_pair_offsets=sorted(masked),
                                       csi_feature_count=2 * len(kept))

    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "samples.jsonl").open("w", encoding="utf-8") as stream:
        for entry in packets:
            packet = entry["packet"]
            lid = layout_ids[layout_key(packet)]
            offsets = layout_schema[lid]["kept_pair_offsets"]
            features = {key: packet[key] for key in kept_scalars}
            features["csi_imag_real"] = [packet["csi_raw"][2 * offset + component]
                                         for offset in offsets for component in (0, 1)]
            result = dict(target={"distance_m": packet["distance_m"]}, features=features,
                          radio_context={key: packet.get(key) for key in context_fields},
                          layout_id=lid,
                          group={"recording_id": packet["recording_id"], "session_id": packet.get("session_id"),
                                 "orientation": packet.get("orientation", "")},
                          source={"file": entry["file"], "line": entry["line"]})
            stream.write(canonical(result) + "\n")
    labels = Counter(entry["packet"]["distance_m"] for entry in packets)
    report = dict(raw_files=len(manifest), completed_recordings=len(recordings),
                  accepted_packets=len(packets), rejected_entries=len(rejected),
                  packets_by_distance_m={str(k): v for k, v in sorted(labels.items())},
                  recordings=recordings, scalar_features=kept_scalars, radio_context_fields=context_fields,
                  fields=field_report,
                  limitations=[
                      "Nonconstant fields are candidates, not proven predictors of position.",
                      "Distance is a one-dimensional label, not an XY room coordinate.",
                      "Blank orientations cannot be recovered or inferred from recording order.",
                      "Never randomly split adjacent packets: hold out whole recording IDs.",
                      "A single acquisition session cannot establish cross-session generalization.",
                      "Exploratory constant masks use this dataset; refit feature selection on training groups before evaluation.",
                      "Pair offsets span training fields; no calibrated subcarrier mapping is assumed.",
                  ])
    write_json(output_dir / "report.json", report)
    write_json(output_dir / "feature_schema.json", dict(version=1, scalar_features=kept_scalars,
                                                       csi_order="imag,real", layouts=layout_schema,
                                                       normalization="none", gain_compensation="none"))
    write_json(raw_dir.parent / "raw_manifest.json", dict(files=manifest, hash_algorithm="sha256"))
    with (output_dir / "rejected.jsonl").open("w", encoding="utf-8") as stream:
        for entry in rejected:
            stream.write(canonical(entry) + "\n")
    return report, layout_schema


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEMO.parent / "Demo_7_0/data/labeled")
    parser.add_argument("--raw-dir", type=Path, default=DEMO / "data/raw")
    parser.add_argument("--output-dir", type=Path, default=DEMO / "data/cleaned")
    parser.add_argument("--no-copy", action="store_true", help="Reprocess the existing raw snapshot")
    args = parser.parse_args()
    if args.raw_dir.resolve() == args.output_dir.resolve() or args.raw_dir.resolve() in args.output_dir.resolve().parents:
        parser.error("Keep processed outputs outside the raw-data folder")
    try:
        if not args.no_copy:
            copy_recordings(args.source, args.raw_dir)
        report, layouts = prepare(args.raw_dir, args.output_dir)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Preparation failed: {exc}\n")
    print(f"Copied raw snapshot: {args.raw_dir}")
    print(f"Prepared {report['accepted_packets']} packets from {report['completed_recordings']} recordings; "
          f"{report['rejected_entries']} rejected entries.")
    print("Scalar features: " + ", ".join(report["scalar_features"]))
    for lid, info in layouts.items():
        print(f"{lid}: {len(info['kept_pair_offsets'])}/{info['original_pair_count']} variable CSI pairs")
    print(f"Report: {args.output_dir / 'report.json'}")


if __name__ == "__main__":
    main()
