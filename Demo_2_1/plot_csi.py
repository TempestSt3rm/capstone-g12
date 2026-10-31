import csv
import matplotlib.pyplot as plt

time_s = []
csi = []

with open("csi_data.csv", "r") as file:
    reader = csv.DictReader(file)

    for row in reader:
        time_s.append(int(row["time_ms"]) / 1000)
        csi.append(float(row["csi_average"]))

plt.plot(time_s, csi)
plt.xlabel("Time (s)")
plt.ylabel("CSI Average Magnitude")
plt.title("ESP32 WiFi CSI Measurement")
plt.show()