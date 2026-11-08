#pragma once

constexpr unsigned RECEIVER_COUNT = 2;

// Advance only when the laptop acknowledges the expected receiver's capture.
class CaptureSequence {
    unsigned receiver_ = 0;
public:
    void begin() { receiver_ = 1; }
    void cancel() { receiver_ = 0; }
    unsigned receiver() const { return receiver_; }
    bool acknowledge(unsigned receiver) {
        if (!receiver_ || receiver != receiver_) return false;
        receiver_ = receiver_ < RECEIVER_COUNT ? receiver_ + 1 : 0;
        return true;
    }
};
