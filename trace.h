// SPDX-License-Identifier: Unlicense
#pragma once
#include <stdint.h>

enum { TraceBegin, TraceGap, TraceAck, TraceWaitStart, TraceWaitEdge,
       TraceWaitEnd, TracePullup, TraceCancel, TraceDone };
enum { TRACE_CAPACITY = 256 };
typedef struct {
    uint32_t time_us, packet, offset_us;
    uint8_t phase, lines;
} TraceEvent;
typedef struct {
    TraceEvent events[TRACE_CAPACITY];
    uint32_t origin, cycles_us, count, dropped;
} Trace;

static inline void trace_add(Trace* trace, uint32_t now, uint32_t release,
                             uint32_t packet, uint8_t phase, uint8_t lines) {
    if(trace->count == TRACE_CAPACITY) { ++trace->dropped; return; }
    TraceEvent* event = &trace->events[trace->count++];
    // Unsigned subtraction handles one DWT wrap. Attempts are shorter than
    // the 67-second wrap period at 64MHz; both deltas must remain below it.
    event->time_us = (now - trace->origin) / trace->cycles_us;
    event->offset_us = (now - release) / trace->cycles_us;
    event->packet = packet;
    event->phase = phase;
    event->lines = lines & 3U;
}
