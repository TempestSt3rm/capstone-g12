#include <Arduino.h>
#include "components/TransmitterApp.h"
#include "components/Diagnostics.h"
#include "radio_config.h"

namespace {
WiFiServer hostServer(demo3::HostPort);
WiFiClient hostSocket;
csi::SerialCommands host(hostSocket);
csi::DirectWifiTransmitter radio(demo3::Radio);
csi::TransmitterApp transmitter(host, radio);
bool hadHost = false;
}
void setup() {
    csi::beginDiagnostics("TX", demo3::SerialBaud);
    if (!transmitter.begin()) Serial.println("Transmitter Wi-Fi initialization failed");
    hostServer.begin();
    hostServer.setNoDelay(true);
    Serial.printf("[TX %lu ms] Laptop TCP listener port=%u; accessible after Wi-Fi connects\n",
        millis(), demo3::HostPort);
}
void loop() {
    if (hadHost && !hostSocket.connected()) {
        Serial.printf("[TX %lu ms] Laptop TCP disconnected\n", millis());
        transmitter.hostDisconnected(); hostSocket.stop(); hadHost=false;
    }
    WiFiClient incoming = hostServer.available();
    if (incoming) {
        if (hostSocket.connected()) incoming.stop();
        else {
            transmitter.hostDisconnected();
            hostSocket=incoming; hostSocket.setNoDelay(true); hadHost=true;
            Serial.printf("[TX %lu ms] Laptop TCP connected from %s:%u\n",
                millis(), hostSocket.remoteIP().toString().c_str(), hostSocket.remotePort());
        }
    }
    transmitter.tick();
    delay(demo3::LoopDelayMs);
}
