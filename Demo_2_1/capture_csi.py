import serial
import csv

PORT = "/dev/cu.usbserial-10"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=1)

with open("csi_data.csv", "w", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(["time_ms", "csi_average"])

    print("Recording CSI...")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            line = ser.readline().decode(errors="ignore").strip()

            if "," in line:
                parts = line.split(",")

                if len(parts) == 2:
                    try:
                        time_ms = int(parts[0])
                        csi_average = float(parts[1])

                        writer.writerow([time_ms, csi_average])
                        file.flush()

                        print(time_ms, csi_average)

                    except ValueError:
                        pass

    except KeyboardInterrupt:
        print("\nRecording stopped.")

ser.close()