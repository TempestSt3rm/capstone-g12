"""Extract raw imaginary/real CSI pairs into one CSV row per measurement."""
import argparse
import csv
import json
from pathlib import Path

# Edit these filenames and run this script without arguments.
# Relative paths are resolved beside this script, not the terminal directory.
INPUT_FILENAME = "../Server/mystery.json"
OUTPUT_FILENAME = "mystery.csv"


def read_records(path):
    text = path.read_text(encoding="utf-8-sig")
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        # The collector writes JSON Lines, even when the extension is .json.
        records = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {number}: {exc.msg}") from exc
        return records
    return document if isinstance(document, list) else [document]


def convert(input_path, output_path):
    input_path, output_path = Path(input_path), Path(output_path)
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Input and output must be different files")
    rows = []
    skipped = 0
    invalid_first_words = 0
    width = None
    for number, record in enumerate(read_records(input_path), start=1):
        if not isinstance(record, dict):
            raise ValueError(f"Record {number}: expected a JSON object")
        if record.get("type") != "CSI":
            skipped += 1
            continue
        data = record.get("csi")
        if not isinstance(data, list) or not data or len(data) % 2:
            raise ValueError(f"Record {number}: CSI must be a nonempty array of pairs")
        if record.get("len") != len(data):
            raise ValueError(f"Record {number}: CSI length does not match metadata")
        if record.get("csi_order") != "imag_real":
            raise ValueError(f"Record {number}: expected imag_real byte order")
        if not all(type(value) is int and -128 <= value <= 127 for value in data):
            raise ValueError(f"Record {number}: expected signed-byte CSI values")
        if width is None:
            width = len(data)
        elif len(data) != width:
            raise ValueError(f"Record {number}: mixed CSI lengths; separate configurations first")
        rows.append(data)
        invalid_first_words += bool(record.get("first_word_invalid"))
    if not rows:
        raise ValueError("Input contains no CSI measurements")

    # Validate all records before opening the output. No metadata columns,
    # normalization or amplitude conversion: preserve the raw byte sequence.
    header = [name for index in range(width // 2)
              for name in (f"img_{index}", f"real_{index}")]
    with output_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"Saved {len(rows)} measurements, {width // 2} pairs each, to {output_path}")
    if skipped:
        print(f"Skipped {skipped} non-CSI records")
    if invalid_first_words:
        print(f"Note: {invalid_first_words} measurements flag the first four bytes invalid; "
              "raw bytes are retained. Keep the source JSON for validity metadata.")
    return len(rows), width


def script_path(filename):
    path = Path(filename)
    return path if path.is_absolute() else Path(__file__).resolve().parent / path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=INPUT_FILENAME)
    parser.add_argument("--output", default=OUTPUT_FILENAME)
    args = parser.parse_args()
    convert(script_path(args.input), script_path(args.output))


if __name__ == "__main__":
    main()
