// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// The checks of tests/host/check-cloud-pending.py for the cloud page's list of waiting saves (BCDCloudPending and BCDCloudPendingList
// in app/Madeira/BCDGamePage.swift, compiled here on their own).

import Foundation
var failures = 0
func check(_ what: String, _ ok: Bool) { print((ok ? "PASS: " : "FAIL: ") + what); if !ok { failures += 1 } }
let size: (Int64) -> String = { "\($0) B" }
var list: [BCDCloudPending] = (1...15).map { BCDCloudPending(name: "save\($0).dat", bytes: Int64($0) * 10, up: true) }
list.append(BCDCloudPending(name: "profile.sav", bytes: 2048, up: false))
let up = BCDCloudPendingList.rows(list, up: true, size: size), down = BCDCloudPendingList.rows(list, up: false, size: size)
check("a direction shows its own saves only, in the order given, with their sizes", down.rows.count == 1 && down.rows[0].title == "profile.sav" && down.rows[0].detail == "2048 B" && down.more == nil)
check("at most twelve rows, and how many more", up.rows.count == 12 && up.rows[0].title == "save1.dat" && up.rows[11].title == "save12.dat" && up.more == "and 3 more")
check("nothing waiting: no rows", BCDCloudPendingList.rows([], up: true, size: size).rows.isEmpty && BCDCloudPendingList.rows([], up: true, size: size).more == nil)
check("the same name in both directions has two ids", BCDCloudPending(name: "a", bytes: 1, up: true).id != BCDCloudPending(name: "a", bytes: 1, up: false).id)
check("Check again is the page's action cloud-check, and says that nothing moves", BCDCloudPendingList.checkAction == "cloud-check" && BCDCloudPendingList.checkDetail.contains("Nothing is moved"))
check("the headings name the cloud", BCDCloudPendingList.heading(up: true, cloud: "Steam Cloud") == "Waiting to go up to Steam Cloud"
      && BCDCloudPendingList.heading(up: false, cloud: "Steam Cloud") == "Waiting to come down from Steam Cloud")
print(failures == 0 ? "ALL PASS" : "FAILED: \(failures)")
exit(failures == 0 ? 0 : 1)
