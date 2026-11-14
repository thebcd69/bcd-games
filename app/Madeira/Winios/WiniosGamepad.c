/* GPL-3.0-or-later WITH the Madeira Converter Exception, version 1. */
/* Portions copyright 2026 The Porthole authors: code taken from https://github.com/tylerkaska112/Porthole (commit 99ec315,
 * patches/0019-pad-read-counters.patch) under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md. */
#include "WiniosGamepad.h"
#include <pthread.h>
#include <string.h>

/* ml1920: protect the payload as well as the version. A sequence counter
 * around a non-atomic struct copy still constitutes a C data race. The lock
 * covers only a 20-byte snapshot, never framework work or a Wine server call. */
static pthread_mutex_t pad_lock = PTHREAD_MUTEX_INITIALIZER;
static struct winios_gamepad pads[WINIOS_GAMEPAD_MAX];

/* madeira-bcd (Porthole port, item 8; from Porthole's patch 0019): how many times the game has asked for each XInput slot (any
 * answer, connected or not). A game that never asks is not reading the controller at all (app/Madeira/BCDPadAuto.swift; only a
 * game with `pad-auto = 1` has a reader of the count). Relaxed atomics: the reader is another thread and only the trend matters.
 * BCD counts the XInput slots only: its HID pad exists only when the player chose that identity, and then the rule stands down. */
static uint64_t pad_reads[WINIOS_GAMEPAD_MAX];

void winios_gamepad_set_state(int index, const struct winios_gamepad *state)
{
    struct winios_gamepad next = {0};
    if (index < 0 || index >= WINIOS_GAMEPAD_MAX) return;
    if (state && state->connected) {
        next = *state;
        next.connected = 1;
        memset(next.reserved, 0, sizeof(next.reserved));
    }
    pthread_mutex_lock(&pad_lock);
    next.packet = pads[index].packet;
    if (memcmp(&next, &pads[index], sizeof(next))) {
        next.packet++;
        pads[index] = next;
    }
    pthread_mutex_unlock(&pad_lock);
}

int winios_gamepad_get_state(int index, struct winios_gamepad *out)
{
    struct winios_gamepad value = {0};
    if (index >= 0 && index < WINIOS_GAMEPAD_MAX) {
        __atomic_fetch_add(&pad_reads[index], 1, __ATOMIC_RELAXED);
        pthread_mutex_lock(&pad_lock);
        value = pads[index];
        pthread_mutex_unlock(&pad_lock);
    }
    if (out) {
        if (value.connected) *out = value;
        else memset(out, 0, sizeof(*out));
    }
    return value.connected != 0;
}

/* madeira-bcd (from Porthole's patch 0019): the read counters above, summed over every XInput slot. */
uint64_t winios_gamepad_read_count(void)
{
    uint64_t total = 0;
    for (int i = 0; i < WINIOS_GAMEPAD_MAX; i++) total += __atomic_load_n(&pad_reads[i], __ATOMIC_RELAXED);
    return total;
}

/* ml2100: the HID controller's snapshot and the game's output state. Their own
 * lock, so the XInput slots above keep exactly the contention they had. The
 * wineserver thread reads the snapshot for every report it builds and writes
 * the output state when a game sends an output report; the app's gamepad
 * queue does the opposite. Both copies are under 50 bytes. */
static pthread_mutex_t hidpad_lock = PTHREAD_MUTEX_INITIALIZER;
static struct winios_hidpad hidpad;
static struct winios_hidpad_output hidpad_output;

void winios_hidpad_set_state(const struct winios_hidpad *state)
{
    struct winios_hidpad next = {0};
    if (state && state->connected) {
        next = *state;
        next.connected = 1;
        memset(next.reserved, 0, sizeof(next.reserved));
        next.reserved2 = 0;
    }
    pthread_mutex_lock(&hidpad_lock);
    next.packet = hidpad.packet;
    if (memcmp(&next, &hidpad, sizeof(next))) {
        next.packet++;
        hidpad = next;
    }
    pthread_mutex_unlock(&hidpad_lock);
}

int winios_hidpad_get_state(struct winios_hidpad *out)
{
    struct winios_hidpad value;
    pthread_mutex_lock(&hidpad_lock);
    value = hidpad;
    pthread_mutex_unlock(&hidpad_lock);
    if (out) {
        if (value.connected) *out = value;
        else {
            /* Keep the packet: a disconnect is a change the reader must see. */
            memset(out, 0, sizeof(*out));
            out->packet = value.packet;
        }
    }
    return value.connected != 0;
}

/* ml2106: the app's "something to apply" hook; see WiniosGamepad.h. */
static winios_pad_output_notify_fn output_notify;

void winios_pad_output_set_notify(winios_pad_output_notify_fn fn)
{
    __atomic_store_n(&output_notify, fn, __ATOMIC_RELEASE);
}

static void notify_output(void)
{
    winios_pad_output_notify_fn fn = __atomic_load_n(&output_notify, __ATOMIC_ACQUIRE);
    if (fn) fn();
}

void winios_hidpad_set_output(const struct winios_hidpad_output *output)
{
    struct winios_hidpad_output next;
    int changed = 0;
    if (!output) return;
    next = *output;
    memset(next.reserved, 0, sizeof(next.reserved));
    pthread_mutex_lock(&hidpad_lock);
    next.serial = hidpad_output.serial;
    if (memcmp(&next, &hidpad_output, sizeof(next))) {
        next.serial++;
        hidpad_output = next;
        changed = 1;
    }
    pthread_mutex_unlock(&hidpad_lock);
    if (changed) notify_output();
}

int winios_hidpad_get_output(uint32_t seen, struct winios_hidpad_output *out)
{
    struct winios_hidpad_output value;
    pthread_mutex_lock(&hidpad_lock);
    value = hidpad_output;
    pthread_mutex_unlock(&hidpad_lock);
    if (out) *out = value;
    return value.serial != seen;
}

/* ml2106: XInput rumble, per slot. Its own lock: XInputSetState may come from
 * any game thread, as often as every frame; only a change notifies. */
static pthread_mutex_t vibration_lock = PTHREAD_MUTEX_INITIALIZER;
static struct winios_gamepad_vibration vibrations[WINIOS_GAMEPAD_MAX];
static int rumble_caps;

void winios_gamepad_set_vibration(int index, uint16_t left, uint16_t right)
{
    int changed = 0;
    if (index < 0 || index >= WINIOS_GAMEPAD_MAX) return;
    pthread_mutex_lock(&vibration_lock);
    if (!vibrations[index].serial || vibrations[index].left != left || vibrations[index].right != right) {
        vibrations[index].left = left;
        vibrations[index].right = right;
        if (!++vibrations[index].serial) vibrations[index].serial = 1;
        changed = 1;
    }
    pthread_mutex_unlock(&vibration_lock);
    if (changed) notify_output();
}

uint32_t winios_gamepad_get_vibration(int index, struct winios_gamepad_vibration *out)
{
    struct winios_gamepad_vibration value = {0};
    if (index >= 0 && index < WINIOS_GAMEPAD_MAX) {
        pthread_mutex_lock(&vibration_lock);
        value = vibrations[index];
        pthread_mutex_unlock(&vibration_lock);
    }
    if (out) *out = value;
    return value.serial;
}

void winios_gamepad_set_rumble_caps(int on)
{
    __atomic_store_n(&rumble_caps, !!on, __ATOMIC_RELAXED);
}

int winios_gamepad_rumble_caps(void)
{
    return __atomic_load_n(&rumble_caps, __ATOMIC_RELAXED);
}
