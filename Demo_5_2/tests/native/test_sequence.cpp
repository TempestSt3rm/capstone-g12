#include <cassert>
#include "CaptureSequence.h"

int main() {
    CaptureSequence sequence;
    assert(sequence.receiver() == 0);
    assert(!sequence.acknowledge(1));
    sequence.begin();
    assert(sequence.receiver() == 1);
    assert(!sequence.acknowledge(2));
    assert(sequence.receiver() == 1);
    assert(sequence.acknowledge(1));
    assert(sequence.receiver() == 2);
    assert(!sequence.acknowledge(1));
    assert(sequence.receiver() == 2);
    assert(sequence.acknowledge(2));
    assert(sequence.receiver() == 0);
    assert(!sequence.acknowledge(3));
    assert(!sequence.acknowledge(2));
    sequence.begin();
    sequence.cancel();
    assert(sequence.receiver() == 0);
    sequence.begin();
    assert(sequence.receiver() == 1);
}
