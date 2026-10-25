#include <Arduino.h>
#include "components/TransmitterApp.h"
#include "radio_config.h"

namespace {
csi::SerialCommands host(Serial);
csi::DirectWifiTransmitter radio(demo3::Radio);
csi::TransmitterApp transmitter(host, radio, demo3::ProbeIntervalUs);
}

void setup() {
    Serial.begin(demo3::SerialBaud); // Laptop data/control connection is on this fixed board.
    transmitter.begin();
}
void loop() {
    transmitter.tick();
    delay(demo3::LoopDelayMs);
}
