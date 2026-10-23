// Compile-only boundaries for the account adapter; never part of an app target.
import Foundation
import UIKit
import Combine

struct LibraryEntry: Identifiable {
    var id = UUID()
    var title: String
    var relativePath: String
    var bits: Int
    var folderBytes: Int64?
    var desktop: Bool?
    var steamAppID: Int?
}
@MainActor final class LibraryModel: ObservableObject {
    static let shared = LibraryModel()
    @Published var entries: [LibraryEntry] = []
    @Published var current: Bool?
    var error: String?
    func save(_ entry: LibraryEntry) {}
    func remove(_ id: UUID) {}
}
@MainActor final class SteamOwnedLibrary: ObservableObject {
    static let shared = SteamOwnedLibrary()
    @Published var downloads: [Int: Int] = [:]
    var hasActiveDownload: Bool { false }
}
@MainActor final class BCDContentBackground {
    static let shared = BCDContentBackground()
    func attach(_ queue: BCDContentQueue) {}
}
@MainActor final class BCDLiveStore {}
enum BCDLiveID { static func local(_ id: UUID) -> String { "local." + id.uuidString } }
func wine_process_is_running() -> Int32 { 0 }
func wineserver_is_running() -> Int32 { 0 }

@MainActor enum BCDWebSignIn {
    static func topController() -> UIViewController? { nil }
}
