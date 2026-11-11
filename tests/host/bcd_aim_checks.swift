// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// The behaviour half of tests/host/check-bcd-aim.py. That script compiles this file as main.swift together with
// app/Madeira/BCDAimZone.swift (plain Foundation and CoreGraphics) and runs it: the settings a game's lines make, which touches
// are in the zone, how a finger's travel becomes whole mouse counts without losing the fractions, and the one finger that aims.
import CoreGraphics
import Foundation

var failures = 0
func require(_ ok: @autoclosure () -> Bool, _ what: String) {
    if ok() { print("PASS: " + what) } else { print("FAIL: " + what); failures += 1 }
}
func aim(_ lines: [String: String]) -> BCDAimSettings { BCDAimSettings.parse { lines[$0] } }

// ---- the settings --------------------------------------------------------------------------------------------------
require(aim([:]) == BCDAimSettings() && !aim([:]).isOn && aim([:]).zone == .off, "no lines: off")
require(aim([:]).sensitivity == 1 && aim([:]).vertical == 100 && !aim([:]).invertY && !aim([:]).tapClicks, "... with the default values")
require(aim(["aim-zone": "right"]).zone == .right && aim(["aim-zone": " LEFT "]).zone == .left && aim(["aim-zone": "all"]).zone == .all, "zones, any case")
require(aim(["aim-zone": "middle"]).zone == .off && aim(["aim-zone": "1"]).zone == .off && !aim(["aim-zone": "on"]).isOn, "anything else is off")
require(aim(["aim-zone": "all"]).isOn, "a zone is on")
require(aim(["aim-sens": "2"]).sensitivity == 2 && aim(["aim-sens": "99"]).sensitivity == 8 && aim(["aim-sens": "0"]).sensitivity == 0.1 && aim(["aim-sens": "x"]).sensitivity == 1
        && aim(["aim-sens": "nan"]).sensitivity == 1 && aim(["aim-sens": "inf"]).sensitivity == 1, "the sensitivity is kept in range, a bad one is the default")
require(aim(["aim-vertical": "150"]).vertical == 150 && aim(["aim-vertical": "5"]).vertical == 25 && aim(["aim-vertical": "900"]).vertical == 200 && aim(["aim-vertical": "?"]).vertical == 100, "the vertical share too")
require(aim(["aim-invert": "1"]).invertY && !aim(["aim-invert": "0"]).invertY && aim(["aim-tap": "1"]).tapClicks && !aim(["aim-tap": ""]).tapClicks, "invert and tap")
require(BCDAimSettings.lineNames.count == 6 && Set(BCDAimSettings.lineNames).count == 6 && BCDAimSettings.lineNames.contains("aim-as"), "six lines (aim-as: night round 2026-11-11)")
require(BCDAimSettings.sensitivityChoices.allSatisfy { BCDAimSettings.sensitivityRange.contains($0) } && BCDAimSettings.verticalChoices.allSatisfy { BCDAimSettings.verticalRange.contains($0) }, "every offered value is in range")
require(BCDAimSettings.Zone.allCases.allSatisfy { !$0.title.isEmpty } && BCDAimSettings.Zone.allCases.count == 4, "four zones with words")

// ---- the zone --------------------------------------------------------------------------------------------------------
let screen = CGSize(width: 800, height: 400)
require(BCDAimMath.contains(CGPoint(x: 401, y: 10), bounds: screen, zone: .right) && !BCDAimMath.contains(CGPoint(x: 399, y: 10), bounds: screen, zone: .right), "the right half")
require(BCDAimMath.contains(CGPoint(x: 399, y: 390), bounds: screen, zone: .left) && !BCDAimMath.contains(CGPoint(x: 400, y: 390), bounds: screen, zone: .left), "the left half")
require(BCDAimMath.contains(CGPoint(x: 0, y: 0), bounds: screen, zone: .all) && BCDAimMath.contains(CGPoint(x: 799, y: 399), bounds: screen, zone: .all), "anywhere")
require(!BCDAimMath.contains(CGPoint(x: 600, y: 200), bounds: screen, zone: .off), "off: nowhere")

// ---- counts ------------------------------------------------------------------------------------------------------------
var carry = BCDAimMath.Carry()
var s = BCDAimSettings(); s.zone = .all
var c = BCDAimMath.counts(dx: 10, dy: -5, s, carry: &carry)
require(c.dx == 20 && c.dy == -10, "sensitivity 1: two counts for each point, as a mouse (right is plus, down is plus)")
s.sensitivity = 2
c = BCDAimMath.counts(dx: 10, dy: 10, s, carry: &carry)
require(c.dx == 40 && c.dy == 40, "sensitivity 2 doubles it")
s.sensitivity = 1; s.vertical = 50
c = BCDAimMath.counts(dx: 10, dy: 10, s, carry: &carry)
require(c.dx == 20 && c.dy == 10, "a vertical share of 50 % halves up and down only")
s.vertical = 100; s.invertY = true
c = BCDAimMath.counts(dx: 10, dy: 10, s, carry: &carry)
require(c.dx == 20 && c.dy == -20, "invert turns the vertical")
s.invertY = false; s.sensitivity = 0.5
carry.reset()
var total = 0
for _ in 0..<7 { total += Int(BCDAimMath.counts(dx: 0.3, dy: 0, s, carry: &carry).dx) }   // 7 x 0.3 pt x 1 count = 2.1
require(total == 2 && abs(carry.x - 0.1) < 1e-9, "slow drags lose nothing: the fraction is carried (2 of 2.1 counts sent, 0.1 kept)")
carry.reset()
var negative = 0
for _ in 0..<7 { negative += Int(BCDAimMath.counts(dx: -0.3, dy: 0, s, carry: &carry).dx) }
require(negative == -2, "... in both directions, rounding toward zero")
carry.reset()
c = BCDAimMath.counts(dx: 1e9, dy: -1e9, s, carry: &carry)
require(c.dx == 30000 && c.dy == -30000, "a huge move is clamped")
c = BCDAimMath.counts(dx: .nan, dy: .infinity, s, carry: &carry)
require(c.dx == 0 && c.dy == 0, "a number that is no number is nothing")

// ---- the aiming finger ---------------------------------------------------------------------------------------------------
var touch = BCDAimTouch()
var live = BCDAimSettings(); live.zone = .right
require(!touch.began(id: 1, at: CGPoint(x: 100, y: 100), time: 0, bounds: screen, live) && !touch.isAiming, "a touch outside the zone does not aim")
require(touch.began(id: 2, at: CGPoint(x: 600, y: 100), time: 0, bounds: screen, live) && touch.isAiming, "a touch in the zone aims")
require(!touch.began(id: 3, at: CGPoint(x: 700, y: 300), time: 0.1, bounds: screen, live), "a second finger does not (one aims at a time)")
require(touch.moved(id: 3, to: CGPoint(x: 710, y: 310), live) == nil && touch.ended(id: 3, time: 0.2) == nil, "... and is none of its business")
let move = touch.moved(id: 2, to: CGPoint(x: 610, y: 105), live)
require(move?.dx == 20 && move?.dy == 10, "the aiming finger turns its travel into counts")
let move2 = touch.moved(id: 2, to: CGPoint(x: 612, y: 105), live)
require(move2?.dx == 4 && move2?.dy == 0, "... from where it last was")
require(touch.ended(id: 2, time: 1.0) == false && !touch.isAiming, "lifting after a drag is no tap")
require(touch.began(id: 4, at: CGPoint(x: 500, y: 100), time: 5, bounds: screen, live), "another finger aims once the first has lifted")
require(touch.ended(id: 4, time: 5.1) == true, "a short, still touch is a tap")
_ = touch.began(id: 5, at: CGPoint(x: 500, y: 100), time: 6, bounds: screen, live)
require(touch.ended(id: 5, time: 6.5) == false, "a still touch held half a second is no tap")
_ = touch.began(id: 6, at: CGPoint(x: 500, y: 100), time: 7, bounds: screen, live)
_ = touch.moved(id: 6, to: CGPoint(x: 530, y: 100), live)
_ = touch.moved(id: 6, to: CGPoint(x: 500, y: 100), live)
require(touch.ended(id: 6, time: 7.1) == false, "a quick touch that went far and came back is no tap")
_ = touch.began(id: 7, at: CGPoint(x: 500, y: 100), time: 8, bounds: screen, live)
require(touch.cancelled(id: 7) && !touch.isAiming && !touch.cancelled(id: 7), "taken away: it stops aiming, once")
_ = touch.began(id: 8, at: CGPoint(x: 500, y: 100), time: 9, bounds: screen, live)
touch.reset()
require(!touch.isAiming && touch.moved(id: 8, to: CGPoint(x: 1, y: 1), live) == nil, "reset forgets the finger")
var off = BCDAimSettings()
require(!touch.began(id: 9, at: CGPoint(x: 500, y: 100), time: 9, bounds: screen, off), "with the zone off no finger aims")
off.zone = .all
_ = touch.began(id: 10, at: CGPoint(x: 5, y: 5), time: 9, bounds: screen, off)
let leftover = touch.moved(id: 10, to: CGPoint(x: 6, y: 5), off)
require(leftover?.dx == 2, "(sanity: with the zone on anywhere a corner aims)")

if failures > 0 { print("\(failures) FAILED"); exit(1) }

// ---- the output (night round 2026-11-11, A5 S13: NieR: Automata 83B2RX) ------------------------------------------------------
require(aim([:]).output == .mouse && aim(["aim-as": ""]).output == .mouse && aim(["aim-as": "mouse"]).output == .mouse && aim(["aim-as": "x"]).output == .mouse,
        "no line, an empty one or a word it does not know: the mouse, as always")
require(aim(["aim-as": "stick"]).output == .stick && aim(["aim-as": " Stick "]).output == .stick, "aim-as = stick, any case")
require(aim(["aim-zone": "right", "aim-as": "stick"]).isOn && !aim(["aim-as": "stick"]).isOn, "the stick output needs a zone like the mouse (the zone decides whether aim mode is on)")
require(BCDAimSettings.Output.allCases.count == 2 && BCDAimSettings.Output.allCases.allSatisfy { !$0.title.isEmpty } && BCDAimSettings.Output.mouse.rawValue == "",
        "two outputs with words; the mouse is the default value, no line")
var st = BCDAimSettings(); st.zone = .all; st.output = .stick
let slow = BCDAimMath.stickPush(dx: 1, dy: 0, st)
require(abs(slow.x - 0.4) < 1e-9 && slow.y == 0, "a drag of 1 point an event is 40 % of the stick to the right")
let down = BCDAimMath.stickPush(dx: 0, dy: 1, st)
require(down.x == 0 && abs(down.y + 0.4) < 1e-9, "dragging down looks down: the stick goes down (negative)")
var inv = st; inv.invertY = true
require(abs(BCDAimMath.stickPush(dx: 0, dy: 1, inv).y - 0.4) < 1e-9, "inverted: dragging down looks up")
var fast = st; fast.sensitivity = 2; fast.vertical = 50
let both = BCDAimMath.stickPush(dx: 0.5, dy: 0.5, fast)
require(abs(both.x - 0.4) < 1e-9 && abs(both.y + 0.2) < 1e-9, "sensitivity 2 and 50 % up and down: 0.4 across, 0.2 down")
let big = BCDAimMath.stickPush(dx: 30, dy: 40, st)
require(abs(big.x * big.x + big.y * big.y - 1) < 1e-9 && big.x > 0 && big.y < 0 && abs(big.x / big.y + 0.75) < 1e-9, "a long swipe is a full push in its direction, never more")
require(BCDAimMath.stickPush(dx: .nan, dy: 1, st) == (0, 0) && BCDAimMath.stickPush(dx: 1, dy: .infinity, st) == (0, 0), "a bad number is no push")
require(BCDAimMath.stickHoldSeconds == 0.05 && BCDAimMath.pointsPerFullPush == 2.5, "50 ms hold, 2.5 points for a full push")
var touch2 = BCDAimTouch()
require(touch2.movedPoints(id: 1, to: CGPoint(x: 5, y: 5)) == nil, "movedPoints: a finger that does not aim gives nothing")
_ = touch2.began(id: 1, at: CGPoint(x: 600, y: 100), time: 0, bounds: screen, st)
let travel = touch2.movedPoints(id: 1, to: CGPoint(x: 603, y: 98))
require(travel?.dx == 3 && travel?.dy == -2 && touch2.movedPoints(id: 2, to: CGPoint(x: 0, y: 0)) == nil, "movedPoints: the travel since the last event for the aiming finger only")

print("ALL PASS")
