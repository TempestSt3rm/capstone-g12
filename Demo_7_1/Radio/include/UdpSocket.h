#pragma once

#include <Arduino.h>
#include <IPAddress.h>
#include <fcntl.h>
#include <lwip/sockets.h>
#include <unistd.h>

// Send each CSI JSON object as ONE datagram, including responses larger than
// 1460 bytes. WiFiUDP's write buffer can split those into separate datagrams.
class UdpSocket {
    int fd_ = -1;

public:
    void close() {
        if (fd_ >= 0) ::close(fd_);
        fd_ = -1;
    }

    bool begin(uint16_t port) {
        close();
        fd_ = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (fd_ < 0) return false;
        sockaddr_in local = {};
        local.sin_family = AF_INET;
        local.sin_port = htons(port);
        local.sin_addr.s_addr = INADDR_ANY;
        if (bind(fd_, reinterpret_cast<sockaddr*>(&local), sizeof(local)) != 0 ||
            fcntl(fd_, F_SETFL, O_NONBLOCK) < 0) {
            close();
            return false;
        }
        return true;
    }

    bool send(const String& message, IPAddress ip, uint16_t port) {
        sockaddr_in remote = {};
        remote.sin_family = AF_INET;
        remote.sin_port = htons(port);
        remote.sin_addr.s_addr = static_cast<uint32_t>(ip);
        return sendto(fd_, message.c_str(), message.length(), 0,
                      reinterpret_cast<sockaddr*>(&remote), sizeof(remote)) ==
               static_cast<int>(message.length());
    }

    int receive(char* data, size_t capacity, IPAddress& ip, uint16_t& port) {
        sockaddr_in source = {};
        socklen_t size = sizeof(source);
        const int len = recvfrom(fd_, data, capacity, 0,
                                 reinterpret_cast<sockaddr*>(&source), &size);
        if (len >= 0) {
            ip = IPAddress(source.sin_addr.s_addr);
            port = ntohs(source.sin_port);
        }
        return len;
    }
};
