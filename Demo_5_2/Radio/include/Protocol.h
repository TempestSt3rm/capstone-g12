#pragma once

#include <Arduino.h>
#include <IPAddress.h>

struct Request {
    uint32_t id = 0;
    IPAddress computer;
    uint16_t port = 0;
};

inline bool parse_number(const String& text, uint32_t& number) {
    if (!text.length() || text.length() > 10) return false;
    uint64_t value = 0;
    for (size_t i = 0; i < text.length(); ++i) {
        if (text[i] < '0' || text[i] > '9') return false;
        value = value * 10 + (text[i] - '0');
        if (value > UINT32_MAX) return false;
    }
    number = static_cast<uint32_t>(value);
    return true;
}

inline bool same_request(const Request& a, const Request& b) {
    return a.id == b.id && a.computer == b.computer && a.port == b.port;
}

// Controls include the laptop's original address/port so RX can reply to it.
inline String control(const char* kind, const Request& request) {
    return String(kind) + " " + request.id + " " + request.computer.toString() +
           " " + request.port;
}

inline bool parse_control(const String& message, const char* kind, Request& request) {
    const String prefix = String(kind) + " ";
    if (!message.startsWith(prefix)) return false;
    const int first = message.indexOf(' ', prefix.length());
    const int second = message.indexOf(' ', first + 1);
    if (first < 0 || second < 0) return false;
    uint32_t port;
    if (!parse_number(message.substring(prefix.length(), first), request.id) ||
        !request.computer.fromString(message.substring(first + 1, second)) ||
        !parse_number(message.substring(second + 1), port) || port < 1 || port > 65535)
        return false;
    request.port = static_cast<uint16_t>(port);
    return true;
}
