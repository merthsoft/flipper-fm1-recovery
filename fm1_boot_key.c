// SPDX-License-Identifier: Unlicense
// ROM boot entry and PC handoff; no flash writes or USB host implementation.
#include <furi.h>
#include <furi_hal.h>
#include <gui/gui.h>
#include <input/input.h>
#include <storage/storage.h>
#include "trace.h"

static const GpioPin dp = {.port = GPIOA, .pin = 1U << 12};
static const GpioPin dm = {.port = GPIOA, .pin = 1U << 11};

typedef struct {
    FuriMessageQueue* input;
    const char* status;
    const char* detail;
    char diagnostics[32];
    char log_status[32];
    Trace* trace;
    uint32_t pulse_count, pulse_late, pulse_max_late_cycles;
} AppState;

static void draw(Canvas* canvas, void* context) {
    AppState* app = context;
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str(canvas, 2, 11, "FM1 Recovery");
    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str(canvas, 2, 25, app->status);
    canvas_draw_str(canvas, 2, 37, app->detail);
    canvas_draw_str(canvas, 2, 49, app->log_status[0] ? app->log_status : "Battery only; no PC/charger");
    canvas_draw_str(canvas, 2, 59, "OK: key  Right: pins  Back: exit");
}

static void input(InputEvent* event, void* context) {
    AppState* app = context;
    furi_message_queue_put(app->input, event, 0);
}

static bool cancelled(AppState* app) {
    InputEvent event;
    while(furi_message_queue_get(app->input, &event, 0) == FuriStatusOk) {
        if(event.key == InputKeyBack && event.type == InputTypePress) return true;
    }
    return false;
}

static void release_bus(void) {
    furi_hal_gpio_init(&dp, GpioModeInput, GpioPullDown, GpioSpeedLow);
    furi_hal_gpio_init(&dm, GpioModeInput, GpioPullDown, GpioSpeedLow);
}

static inline void wait_cycles(uint32_t deadline) {
    while((int32_t)(DWT->CYCCNT - deadline) < 0) __NOP();
}

static uint8_t read_lines(void) {
    uint32_t pins = GPIOA->IDR;
    return ((pins & dp.pin) ? 1U : 0U) | ((pins & dm.pin) ? 2U : 0U);
}

static void capture(AppState* app, uint32_t release, uint32_t packet, uint8_t phase, uint8_t lines) {
    trace_add(app->trace, DWT->CYCCNT, release, packet, phase, lines);
}

static bool write_text(File* file, const char* text) {
    size_t len = strlen(text);
    return storage_file_write(file, text, len) == len;
}

static void save_trace(AppState* app, bool detected, bool abort, bool pins_only) {
    // All SD operations happen AFTER restoring USB; never stall signal timing
    // on filesystem I/O. Preserve previous runs with unique, create-new files.
    const char* dir = EXT_PATH("apps_data/fm1_boot_key");
    Storage* storage = furi_record_open(RECORD_STORAGE);
    FS_Error mkdir_result = storage_common_mkdir(storage, dir);
    FuriString* name = furi_string_alloc();
    FuriString* path = furi_string_alloc();
    File* file = storage_file_alloc(storage);
    bool ok = mkdir_result == FSE_OK || mkdir_result == FSE_EXIST;
    if(ok) {
        storage_get_next_filename(storage, dir, "trace", ".csv", name, 32);
        furi_string_printf(path, "%s/%s.csv", dir, furi_string_get_cstr(name));
        ok = storage_file_open(file, furi_string_get_cstr(path), FSAM_WRITE, FSOM_CREATE_NEW);
    }
    char row[192];
    if(ok) {
        snprintf(row, sizeof(row), "# version=0.7,cpu_cycles_per_us=%lu,count=%lu,dropped=%lu,detected=%u,cancelled=%u,pins_only=%u\n",
                 (unsigned long)app->trace->cycles_us, (unsigned long)app->trace->count,
                 (unsigned long)app->trace->dropped, detected, abort, pins_only);
        ok = write_text(file, row) &&
             write_text(file, "# ") && write_text(file, app->diagnostics) && write_text(file, "\n");
        snprintf(row, sizeof(row), "# calibration_pulses=%lu,late_over_1us=%lu,max_late_cycles=%lu\n",
                 (unsigned long)app->pulse_count, (unsigned long)app->pulse_late,
                 (unsigned long)app->pulse_max_late_cycles);
        ok = ok && write_text(file, row) && write_text(file, "time_us,packet,after_release_us,phase,dp,dm\n");
    }
    static const char* phases[] = {"begin", "gap", "ack_candidate", "wait_start",
                                   "wait_edge", "wait_end", "pullup", "cancel", "done"};
    for(uint32_t i = 0; ok && i < app->trace->count; ++i) {
        TraceEvent* e = &app->trace->events[i];
        snprintf(row, sizeof(row), "%lu,%lu,%lu,%s,%u,%u\n",
                 (unsigned long)e->time_us, (unsigned long)e->packet,
                 (unsigned long)e->offset_us, phases[e->phase], e->lines & 1U,
                 (e->lines >> 1) & 1U);
        ok = write_text(file, row);
    }
    bool closed = storage_file_close(file); // Required even after failed open.
    ok = ok && closed;
    snprintf(app->log_status, sizeof(app->log_status), ok ? "SD: %.18s.csv" : "SD log save FAILED",
             furi_string_get_cstr(name));
    storage_file_free(file);
    furi_string_free(path);
    furi_string_free(name);
    furi_record_close(RECORD_STORAGE);
}

static uint32_t key_packet(uint32_t cycles_us) {
    // Transporter's tested polarity A: D+ clock, D- data, MSB first, 0x16EF.
    // Short critical section avoids OS scheduling between clock edges.
    FURI_CRITICAL_ENTER();
    const uint32_t mode_mask = (3U << 22) | (3U << 24);
    const uint32_t pins = dp.pin | dm.pin;
    GPIOA->BSRR = pins << 16;
    GPIOA->OTYPER &= ~pins;
    GPIOA->PUPDR = (GPIOA->PUPDR & ~mode_mask) | (2U << 22) | (2U << 24);
    GPIOA->MODER = (GPIOA->MODER & ~mode_mask) | (1U << 22) | (1U << 24);
    uint32_t start = DWT->CYCCNT;
    uint32_t deadline = start + 3U * cycles_us;
    wait_cycles(deadline);
    for(int bit = 15; bit >= 0; --bit) {
        bool data = (0x16EFU >> bit) & 1U;
        GPIOA->BSRR = ((uint32_t)dp.pin << 16) |
                       (data ? dm.pin : (uint32_t)dm.pin << 16);
        deadline += 8U * cycles_us;
        wait_cycles(deadline);
        GPIOA->BSRR = dp.pin;
        deadline += (bit ? 12U : 10U) * cycles_us;
        wait_cycles(deadline);
    }
    // Match PIO's final falling clock edge before both outputs are released.
    GPIOA->BSRR = (uint32_t)dp.pin << 16;
    GPIOA->MODER &= ~mode_mask;
    uint32_t elapsed = DWT->CYCCNT - start;
    FURI_CRITICAL_EXIT();
    return elapsed / cycles_us;
}

static void attempt(AppState* app, ViewPort* view, bool pins_only) {
    app->log_status[0] = 0;
    app->pulse_count = app->pulse_late = app->pulse_max_late_cycles = 0;
    if(furi_hal_power_is_otg_enabled() || furi_hal_power_get_usb_voltage() > 3.0f) {
        app->status = "External USB power detected";
        app->detail = "Disconnect PC/charger first";
        return;
    }
    if(furi_hal_usb_is_locked()) {
        app->status = "USB busy; no signal sent";
        app->detail = "Exit other USB apps first";
        return;
    }
    uint32_t cycles_us = furi_hal_cortex_instructions_per_microsecond();
    if(!cycles_us || !(DWT->CTRL & DWT_CTRL_CYCCNTENA_Msk)) {
        app->status = "Cycle timer unavailable";
        app->detail = "No signal sent";
        return;
    }

    // Snapshot only the two pins; do not disturb other GPIOA users.
    const uint32_t mode_mask = (3U << 22) | (3U << 24);
    const uint32_t pin_mask = (1U << 11) | (1U << 12);
    const uint32_t af_mask = (15U << 12) | (15U << 16);
    uint32_t mode = GPIOA->MODER & mode_mask;
    uint32_t type = GPIOA->OTYPER & pin_mask;
    uint32_t speed = GPIOA->OSPEEDR & mode_mask;
    uint32_t pull = GPIOA->PUPDR & mode_mask;
    uint32_t af = GPIOA->AFR[1] & af_mask;
    uint32_t output = GPIOA->ODR & pin_mask;
    furi_hal_power_insomnia_enter();
    furi_hal_usb_disable();
    furi_hal_usb_lock();
    // Disconnecting only clears the USB pull-up. The active USB transceiver
    // still owns these pins regardless of GPIO MODER; power it down explicitly.
    bool lp_irq = NVIC_GetEnableIRQ(USB_LP_IRQn);
    bool hp_irq = NVIC_GetEnableIRQ(USB_HP_IRQn);
    NVIC_DisableIRQ(USB_LP_IRQn);
    NVIC_DisableIRQ(USB_HP_IRQn);
    USB->CNTR = USB_CNTR_FRES | USB_CNTR_PDWN;
    USB->ISTR = 0;
    __DSB();
    release_bus();
    app->status = "Sending key; turn FM1 on";
    app->detail = "Back cancels";
    view_port_update(view);

    bool detected = false;
    bool abort = false;
    bool data_floated = false;
    unsigned packets = 0;
    unsigned ack_candidates = 0;
    unsigned dm_highs = 0;
    unsigned packet_us = 0;
    bool low_episode = false;
    uint8_t previous_gap = 0;
    unsigned pin_high = 0;
    unsigned pin_low = 0;
    if(pins_only) {
        // No push-pull output. On an unplugged port both pins should follow
        // their weak internal pulls if GPIO ownership/input reads are working.
        furi_hal_gpio_init(&dp, GpioModeInput, GpioPullUp, GpioSpeedLow);
        furi_hal_gpio_init(&dm, GpioModeInput, GpioPullUp, GpioSpeedLow);
        furi_delay_us(200);
        pin_high = (furi_hal_gpio_read(&dp) ? 1U : 0U) |
                   (furi_hal_gpio_read(&dm) ? 2U : 0U);
        release_bus();
        furi_delay_us(200);
        pin_low = (furi_hal_gpio_read(&dp) ? 1U : 0U) |
                  (furi_hal_gpio_read(&dm) ? 2U : 0U);
    }
    uint32_t start = furi_get_tick();
    memset(app->trace, 0, sizeof(Trace));
    app->trace->origin = DWT->CYCCNT;
    app->trace->cycles_us = cycles_us;
    uint32_t release = app->trace->origin;
    capture(app, release, 0, TraceBegin, read_lines());
    while(!pins_only && furi_get_tick() - start < furi_ms_to_ticks(15000)) {
        if((abort = cancelled(app))) break;
        if(furi_hal_gpio_read(&dp)) { detected = true; break; }
        packet_us = key_packet(cycles_us);
        release = DWT->CYCCNT;
        ++packets;
        // Weak input pull-up observes a held-low acknowledgement without driving
        // push-pull high against the FM1. This differs from Transporter's 2us probe.
        furi_hal_gpio_init(&dm, GpioModeInput, GpioPullUp, GpioSpeedLow);
        unsigned lows = 0;
        unsigned highs = 0;
        for(unsigned probe = 0; probe < 4; ++probe) {
            furi_delay_us(35);
            uint8_t lines = read_lines();
            // Log changes across released-bus samples, not thousands of copies
            // of an unpowered device holding D- low before its power switch.
            if(lines != previous_gap || (packets == 1 && probe == 0))
                capture(app, release, packets, TraceGap, lines);
            previous_gap = lines;
            highs = (lines & 1U) ? highs + 1 : 0;
            if(highs >= 2) { detected = true; break; }
            if(lines & 2U) {
                data_floated = true;
                ++dm_highs;
                lows = 0;
                low_episode = false;
            } else if(data_floated && ++lows >= 2 && !low_episode) {
                low_episode = true;
                ++ack_candidates;
                capture(app, release, packets, TraceAck, lines);
                // A low D- is inconclusive (off-state loading also causes it).
                // Keep keying: only observed D+ pull-up starts calibration.
            }
        }
        release_bus();
        if(detected || abort) break;
    }

    if(detected) {
        capture(app, release, packets, TracePullup, read_lines());
        app->status = "D+ response; calibration";
        app->detail = "Wait for cable handoff cue";
        view_port_update(view);
        unsigned low_samples = 0;
        // Fixed deadlines prevent HAL/queue overhead accumulating into the ROM
        // USB oscillator's calibration period. Only the final 10us lead-in and
        // 4us low pulse mask interrupts; OS scheduling remains available.
        GPIOA->OTYPER |= dp.pin;
        GPIOA->BSRR = (uint32_t)dp.pin << 16;
        // Keep tasks from preempting a pulse by a whole tick. IRQs remain on.
        // Suspended FreeRTOS ticks are deferred, so use DWT for the bound and
        // read Back directly instead of relying on the suspended input task.
        int32_t previous_lock = furi_kernel_lock();
        furi_check(previous_lock >= 0);
        uint32_t calibration_start = DWT->CYCCNT;
        uint32_t pulse_deadline = calibration_start + 1000U * cycles_us;
        while(DWT->CYCCNT - calibration_start < 6000000U * cycles_us) {
            if(!furi_hal_gpio_read(&gpio_button_back)) { abort = true; break; }
            wait_cycles(pulse_deadline - 10U * cycles_us);
            FURI_CRITICAL_ENTER();
            wait_cycles(pulse_deadline);
            uint32_t pulse_start = DWT->CYCCNT;
            uint32_t lateness = pulse_start - pulse_deadline;
            GPIOA->MODER = (GPIOA->MODER & ~(3U << 24)) | (1U << 24);
            wait_cycles(pulse_start + 4U * cycles_us);
            GPIOA->MODER &= ~(3U << 24);
            FURI_CRITICAL_EXIT();
            ++app->pulse_count;
            if(lateness > cycles_us) ++app->pulse_late;
            if(lateness > app->pulse_max_late_cycles) app->pulse_max_late_cycles = lateness;
            pulse_deadline += 1000U * cycles_us;
            // If a whole frame was missed, do not issue a burst of catch-up
            // pulses. Advance to the next original-grid deadline instead.
            while((int32_t)(DWT->CYCCNT - pulse_deadline) >= 0)
                pulse_deadline += 1000U * cycles_us;
            wait_cycles(pulse_start + 100U * cycles_us);
            low_samples = furi_hal_gpio_read(&dp) ? 0 : low_samples + 1;
            if(DWT->CYCCNT - calibration_start >= 1000000U * cycles_us && low_samples >= 3) break;
        }
        furi_check(furi_kernel_restore_lock(previous_lock) >= 0);
    }
    capture(app, release, packets, abort ? TraceCancel : TraceDone, read_lines());
    release_bus();
    FURI_CRITICAL_ENTER();
    GPIOA->BSRR = output | ((pin_mask & ~output) << 16);
    GPIOA->OTYPER = (GPIOA->OTYPER & ~pin_mask) | type;
    GPIOA->OSPEEDR = (GPIOA->OSPEEDR & ~mode_mask) | speed;
    GPIOA->PUPDR = (GPIOA->PUPDR & ~mode_mask) | pull;
    GPIOA->AFR[1] = (GPIOA->AFR[1] & ~af_mask) | af;
    GPIOA->MODER = (GPIOA->MODER & ~mode_mask) | mode;
    FURI_CRITICAL_EXIT();
    NVIC_ClearPendingIRQ(USB_LP_IRQn);
    NVIC_ClearPendingIRQ(USB_HP_IRQn);
    if(lp_irq) NVIC_EnableIRQ(USB_LP_IRQn);
    if(hp_irq) NVIC_EnableIRQ(USB_HP_IRQn);
    furi_hal_usb_unlock();
    // Power-down reset invalidates endpoint state; enable alone is insufficient.
    furi_hal_usb_reinit();
    furi_hal_usb_enable();
    furi_hal_power_insomnia_exit();
    if(pins_only) {
        app->status = pin_high == 3 && pin_low == 0 ? "USB GPIO pull check passed" : "USB GPIO pull check FAILED";
        snprintf(app->diagnostics, sizeof(app->diagnostics), "H%u L%u (expect H3 L0)", pin_high, pin_low);
        app->detail = app->diagnostics;
        save_trace(app, false, false, true);
        return;
    }
    app->status = abort ? "Cancelled; USB restored" : detected ? "Move FM1 cable to PC now" : "No D+ response detected";
    snprintf(app->diagnostics, sizeof(app->diagnostics), "P%u A%u D%u T%uus", packets, ack_candidates, dm_highs, packet_us);
    app->detail = app->diagnostics;
    save_trace(app, detected, abort, false);
}

int32_t fm1_boot_key_app(void* context) {
    AppState app = {
        .input = furi_message_queue_alloc(16, sizeof(InputEvent)),
        .status = "FM1 OFF, connect to Flipper",
        .detail = "Right: pin check, no cable",
        .trace = malloc(sizeof(Trace)),
    };
    furi_check(app.trace);
    if(context && strcmp(context, "log-test") == 0) {
        // Fixture verifies SD serialization/unique filenames while connected to
        // PC. It never calls the USB/GPIO takeover or key-sending routines.
        memset(app.trace, 0, sizeof(Trace));
        app.trace->origin = UINT32_MAX - 127;
        app.trace->cycles_us = 64;
        trace_add(app.trace, 128, 64, 1, TraceGap, 0);
        trace_add(app.trace, 768, 64, 1, TraceWaitEdge, 2);
        trace_add(app.trace, 896, 64, 1, TraceDone, 2);
        snprintf(app.diagnostics, sizeof(app.diagnostics), "SYNTHETIC SD TEST, no signals");
        app.status = "SD logger self-test";
        app.detail = app.diagnostics;
        save_trace(&app, false, false, false);
    }
    ViewPort* view = view_port_alloc();
    view_port_draw_callback_set(view, draw, &app);
    view_port_input_callback_set(view, input, &app);
    Gui* gui = furi_record_open(RECORD_GUI);
    gui_add_view_port(gui, view, GuiLayerFullscreen);
    InputEvent event;
    while(furi_message_queue_get(app.input, &event, FuriWaitForever) == FuriStatusOk) {
        if(event.type != InputTypeShort) continue;
        if(event.key == InputKeyBack) break;
        if(event.key == InputKeyOk || event.key == InputKeyRight) {
            attempt(&app, view, event.key == InputKeyRight);
            view_port_update(view);
        }
    }
    view_port_enabled_set(view, false);
    gui_remove_view_port(gui, view);
    view_port_free(view);
    furi_record_close(RECORD_GUI);
    furi_message_queue_free(app.input);
    free(app.trace);
    return 0;
}
