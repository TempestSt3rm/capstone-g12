#include <Arduino.h>
#include "components/ReceiverApp.h"
#include "components/SerialCommands.h"
#include "components/Diagnostics.h"
#include "radio_config.h"

namespace {
csi::SerialCommands diagnostics(Serial);
csi::DirectWifiLink link(demo3::Radio);
csi::CsiCapture capture(demo3::captureSettings());
csi::ReceiverApp receiver(link, capture, demo3::Receiver);
}

void setup() {
    csi::beginDiagnostics("RX", demo3::SerialBaud);
    receiver.begin();
}
void loop() {
    String line;
    if (diagnostics.readLine(line) == csi::LineResult::Complete)
        diagnostics.respondAlive(line, "receiver");
    receiver.tick();
    delay(demo3::LoopDelayMs);
}
