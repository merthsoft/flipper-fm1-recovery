// SPDX-License-Identifier: Unlicense
#include <assert.h>
#include <stdio.h>
#include "../trace.h"

int main(void) {
    // DWT may wrap during a run. Both timestamps must keep the right delta.
    Trace trace = {.origin = UINT32_MAX - 127, .cycles_us = 64};
    trace_add(&trace, 128, 64, 17, TraceGap, 2);
    assert(trace.count == 1 && trace.events[0].time_us == 4);
    assert(trace.events[0].offset_us == 1 && trace.events[0].packet == 17);
    assert(trace.events[0].lines == 2 && trace.events[0].phase == TraceGap);
    // Saturation preserves the earliest evidence without writing past storage.
    for(unsigned i = 1; i < TRACE_CAPACITY + 31; ++i)
        trace_add(&trace, 128 + i * 64, 64, i, TraceWaitEdge, 0xff);
    assert(trace.count == TRACE_CAPACITY && trace.dropped == 31);
    assert(trace.events[0].packet == 17);
    assert(trace.events[TRACE_CAPACITY - 1].lines == 3);
    assert(trace.events[TRACE_CAPACITY - 1].packet == TRACE_CAPACITY - 1);
    puts("Flipper trace wrap and capacity tests passed");
    return 0;
}
