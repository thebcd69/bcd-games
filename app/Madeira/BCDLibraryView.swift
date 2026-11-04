// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

/// Library: every game the stores say you own and every game you added, installed or not,
/// filtered by store, sorted as the classic library sorted (last played, name, recently added, folder
/// size) and found by name. The filter and the order stay as they were set, across games and launches. Signed out of Steam, a card offers the
/// sign-in; with no game added by hand, a line says how games get into drive_c.
struct BCDLibraryView: View {
    @EnvironmentObject private var store: BCDStore
    @Environment(\.bcdLayout) private var layout
    /// Y in the shell (BCDFrontEnd): each press opens the search, or closes an open one.
    var searchRequest = 0
    @AppStorage("bcd.library.filter") private var filterKey = BCDLibraryFilter.all.rawValue
    /// The order: BCDLibrarySort's key. Before the sort menu there was only a switch between recent
    /// play and name, kept in `sortByName`; it still decides until a sort is chosen from the menu.
    @AppStorage("bcd.library.sort") private var sortKey = ""
    @AppStorage("bcd.library.sortByName") private var sortByName = false
    @State private var searching = false
    @State private var query = ""
    @FocusState private var typing: Bool
    @State private var opened: BCDGame?
    @Environment(\.bcdMenu) private var menu

    private var filter: BCDLibraryFilter { BCDLibraryFilter.allCases.first { $0.rawValue == filterKey } ?? .all }
    private var sort: BCDLibrarySort { BCDLibrarySort(rawValue: sortKey) ?? (sortByName ? .name : .played) }
    /// The launcher whose chip is chosen (BCDLaunchers.swift); nil for every other filter.
    private var launcher: BCDLauncherSpec? {
        if case .launcher(let id) = filter { return BCDLauncherSpec.spec(id) }
        return nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            filters
            if searching { searchField }
            ScrollView(.vertical, showsIndicators: false) {
                VStack(alignment: .leading, spacing: 16) {
                    if let launcher { BCDLauncherPanel(spec: launcher) }   // a launcher's chip (Battle.net, EA app): its Install / Open card, no games
                    notices
                    savesNotice
                    LazyVGrid(columns: columns, alignment: .leading, spacing: layout == .pad ? 26 : 20) {
                        ForEach(shown) { game in
                            BCDCoverCard(game: game, focusID: "library.game.\(game.id)") { opened = game }
                        }
                    }
                    .bcdGlowLayer()
                    if !query.isEmpty && shown.isEmpty {
                        Text("No game called \"\(query)\" here.").font(BCDStyle.body(15)).foregroundStyle(BCDStyle.textSoft)
                    }
                    if showsDriveHint {
                        Label("To add a game of your own, copy its folder into BCD › wine › drive_c with the Files app, then choose Add a game.",
                              systemImage: "folder.badge.plus")
                            .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(.horizontal, layout.gutter).padding(.top, 14).padding(.bottom, 22)
            }
            .modifier(BCDSoftTopEdge())
            .refreshable { store.actions?.refreshLibrary() }
        }
        .padding(.top, 4)
        .bcdGamePage($opened, from: "Library")
        // The game menu's "Game page" opens the page over the Library.
        .onAppear { menu?.openPage = { opened = $0 } }
        .onChange(of: searchRequest) { _, _ in toggleSearch() }
    }

    // MARK: Filters and the row's buttons

    /// The filters scroll sideways; Add a game, the order, Search and Refresh stay in sight at the end
    /// of the row (Add a game and the order as round buttons alone on an upright phone).
    private var filters: some View {
        HStack(spacing: 8) {
            chips
            rowButton("library.add", "Add a game", "plus") { store.actions?.addGameFromFiles() }
            rowButton("library.sort", sort.title, "arrow.up.arrow.down", label: "Sort by: \(sort.title)") { showSortMenu() }
            round("library.search", searching ? "xmark" : "magnifyingglass", searching ? "Close the search" : "Search") { toggleSearch() }
            round("library.refresh", "arrow.clockwise", "Refresh the library") { store.actions?.refreshLibrary() }
        }
        .padding(.trailing, layout.gutter)
    }

    private var chips: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(availableFilters) { item in
                        Button { filterKey = item.rawValue } label: {
                            Text(item.isLauncher ? item.title : "\(item.title) · \(count(item))")
                                .font(BCDStyle.label(14))
                                .padding(.horizontal, 14).padding(.vertical, 8)
                                .foregroundStyle(filter == item ? BCDStyle.text : BCDStyle.textSoft)
                                .background(filter == item ? BCDStyle.surfaceRaised : Color.clear, in: Capsule())
                                .overlay(Capsule().strokeBorder(filter == item ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.stroke),
                                                                lineWidth: filter == item ? 2 : 1))
                        }
                        .buttonStyle(.plain)
                        .bcdFocusable("library.filter.\(item.rawValue)", cornerRadius: 18) { filterKey = item.rawValue }
                        .id(item.rawValue)
                    }
                }
                .padding(.leading, layout.gutter).padding(.trailing, 4).padding(.vertical, 6)
            }
            // The launchers' chips sit at the end of a row that scrolls: a chosen one is brought into view (the other chips stay where they were).
            .onAppear { if filter.isLauncher { proxy.scrollTo(filter.rawValue, anchor: .center) } }
            .onChange(of: filterKey) { _, _ in if filter.isLauncher { proxy.scrollTo(filter.rawValue, anchor: .center) } }
        }
    }

    private func round(_ id: String, _ symbol: String, _ label: String, _ run: @escaping () -> Void) -> some View {
        Button(action: run) {
            Image(systemName: symbol).font(.system(size: 15, weight: .semibold))
                .foregroundStyle(BCDStyle.text)
                .frame(width: 38, height: 38)
                .background(BCDStyle.surface, in: Circle())
                .overlay(Circle().strokeBorder(BCDStyle.stroke, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .bcdFocusable(id, cornerRadius: 19, action: run)
        .accessibilityLabel(label)
    }

    /// A button at the end of the row: a capsule with its words, a round icon on an upright phone. One
    /// view in both, so turning the phone does not replace it (a control that goes takes its ring and
    /// its controller action with it).
    private func rowButton(_ id: String, _ title: String, _ symbol: String, label: String? = nil,
                           _ run: @escaping () -> Void) -> some View {
        let round = layout == .phonePortrait
        return Button(action: run) {
            Group {
                if round {
                    Image(systemName: symbol).font(.system(size: 15, weight: .semibold))
                        .frame(width: 38, height: 38)
                } else {
                    Label(title, systemImage: symbol)
                        .font(BCDStyle.label(14))
                        .padding(.horizontal, 14).frame(height: 38)
                }
            }
            .foregroundStyle(BCDStyle.text)
            .background(BCDStyle.surface, in: Capsule())
            .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .bcdFocusable(id, cornerRadius: 19, action: run)
        .accessibilityLabel(label ?? title)
    }

    /// Every sort the classic library had, in BCD's own menu (the controller's A on the chip opens it too).
    private func showSortMenu() {
        let current = sort
        menu?.show(BCDMenuContent(title: "Sort by", items: BCDLibrarySort.allCases.map { item in
            BCDMenuItem(id: item.rawValue, title: item.title, symbol: item.symbol, checked: item == current, run: { sortKey = item.rawValue })
        }))
    }

    private var searchField: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass").foregroundStyle(BCDStyle.textSoft)
            TextField("Find a game", text: $query)
                .font(BCDStyle.body(16))
                .focused($typing)
                .submitLabel(.search)
                .autocorrectionDisabled()
            if !query.isEmpty {
                Button { query = "" } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(BCDStyle.textSoft)
                }
                .buttonStyle(.plain)
                .bcdFocusable("library.search.clear", cornerRadius: 12) { query = "" }
                .accessibilityLabel("Clear")
            }
            Button("Done") { toggleSearch() }
                .font(BCDStyle.label(14)).buttonStyle(.plain).foregroundStyle(BCDStyle.lavender)
                .bcdFocusable("library.search.done", cornerRadius: 12) { toggleSearch() }
        }
        .padding(.horizontal, 14).frame(height: 44)
        .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
        .padding(.horizontal, layout.gutter)
    }

    /// Opens the search and the keyboard; closes it again, and forgets the words.
    private func toggleSearch() {
        withAnimation(.easeOut(duration: 0.18)) {
            searching.toggle()
            if !searching { query = "" }
        }
        typing = searching
    }

    // MARK: What the library says above and below its games

    @ViewBuilder private var notices: some View {
        let steam = store.accounts.first { $0.store == .steam }
        let steamShown = filter == .all || filter == .store(.steam)
        if steamShown, let steam, steam.available, steam.name == nil {
            Button { store.actions?.signIn(.steam) } label: {
                HStack(spacing: 14) {
                    Image(systemName: "person.crop.circle.badge.plus").font(.system(size: 28, weight: .semibold))
                        .foregroundStyle(BCDStyle.lavender)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Sign in to Steam").font(BCDStyle.heading(17))
                        Text("Your Steam games show up here, and BCD installs them itself.")
                            .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.right").font(.system(size: 13, weight: .bold)).foregroundStyle(BCDStyle.textSoft)
                }
                .padding(16)
                .frame(maxWidth: 620, alignment: .leading)
                .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).strokeBorder(BCDStyle.glow, lineWidth: 1))
            }
            .buttonStyle(.plain)
            .bcdFocusable("library.signin", cornerRadius: 18) { store.actions?.signIn(.steam) }
        } else if steamShown, let steam, steam.name != nil, store.count(.steam) == 0, query.isEmpty {
            Text("There are no Windows games on this Steam account yet. Refresh reads the account's list again.")
                .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true)
        } else if steamShown, let steam, steam.name != nil, query.isEmpty,
                  store.games.contains(where: { $0.store == .steam }),
                  !store.games.contains(where: { $0.store == .steam && $0.state.isInstalled }) {
            // round 624 (agent N S10, 2FJV7P): a Steam account with games, none installed: how a Steam game starts
            Text("None of your Steam games is installed yet. Tap one, then Install; BCD downloads it from Steam and Play starts it with Valve's own Steam client.")
                .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Games whose Steam Cloud saves differ from this device's, one line each as the classic library's
    /// Steam section said it: nothing is replaced until the player picks the copies to keep on the game's page.
    @ViewBuilder private var savesNotice: some View {
        let steamShown = filter == .all || filter == .store(.steam)
        let games = store.savesDiffer.compactMap { id in store.games.first { $0.id == id } }
            .sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
        if steamShown, query.isEmpty, !games.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(games.prefix(3)) { game in
                    Button { opened = game } label: {
                        HStack(spacing: 12) {
                            Image(systemName: "exclamationmark.icloud").font(.system(size: 20, weight: .semibold))
                                .foregroundStyle(BCDGameColors.amber)
                            Text("Steam Cloud and this device have different saves for \(game.title). Open its page and pick the copy to keep.")
                                .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.text)
                                .multilineTextAlignment(.leading)
                                .fixedSize(horizontal: false, vertical: true)
                            Spacer(minLength: 8)
                            Image(systemName: "chevron.right").font(.system(size: 13, weight: .bold)).foregroundStyle(BCDStyle.textSoft)
                        }
                        .padding(.horizontal, 16).padding(.vertical, 12)
                        .frame(maxWidth: 620, alignment: .leading)
                        .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(BCDGameColors.amber.opacity(0.55), lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    .bcdFocusable("library.saves.\(game.id)", cornerRadius: 16) { opened = game }
                }
                if games.count > 3 {
                    Text("and \(games.count - 3) more").font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                }
            }
        }
    }

    /// No game added by hand yet (the Wine desktop is always there): say how they get in.
    private var showsDriveHint: Bool {
        (filter == .all || filter == .store(.local)) && query.isEmpty && store.count(.local) <= 1
    }

    private var availableFilters: [BCDLibraryFilter] {
        BCDLibraryFilter.allCases.filter { item in
            switch item {
            case .all, .installed: return true
            case .store(let s): return store.count(s) > 0 || store.accounts.contains { $0.store == s && $0.available }
            case .launcher: return BCDLauncherModel.shared.available
            }
        }
    }

    private func count(_ item: BCDLibraryFilter) -> Int {
        switch item {
        case .all: return store.games.count
        case .installed: return store.installedCount
        case .store(let s): return store.count(s)
        case .launcher: return 0
        }
    }

    private var shown: [BCDGame] {
        var list = store.games.filter { game in
            switch filter {
            case .all: return true
            case .installed: return game.state.isInstalled
            case .store(let s): return game.store == s
            case .launcher: return false   // a launcher's chip shows its card, not games
            }
        }
        let words = query.trimmingCharacters(in: .whitespaces)
        if !words.isEmpty { list = list.filter { $0.title.localizedCaseInsensitiveContains(words) } }
        return sort.sorted(list)
    }

    // MARK: The grid

    /// Covers about 100 points wide on a phone on its side (six on an iPhone 13 Pro, seven on a
    /// Pro Max), three across upright, and larger ones on an iPad.
    private var columns: [GridItem] {
        switch layout {
        case .phonePortrait: return Array(repeating: GridItem(.flexible(), spacing: 14, alignment: .top), count: 3)
        case .phoneLandscape: return [GridItem(.adaptive(minimum: 96, maximum: 140), spacing: 14, alignment: .top)]
        case .pad: return [GridItem(.adaptive(minimum: 132, maximum: 184), spacing: 20, alignment: .top)]
        }
    }
}

/// The classic library's Sort by choices (Library.swift: "played", "name", "added", "size"), kept
/// by the same words. Ties go by name, then id.
enum BCDLibrarySort: String, CaseIterable, Identifiable {
    case played, name, added, size
    var id: String { rawValue }
    var title: String {
        switch self {
        case .played: return "Last played"
        case .name: return "Name"
        case .added: return "Recently added"
        case .size: return "Folder size"
        }
    }
    var symbol: String {
        switch self {
        case .played: return "clock"
        case .name: return "textformat"
        case .added: return "plus"
        case .size: return "internaldrive"
        }
    }

    /// Last played: the games on this device first, the most recently played at the top. Name: A to Z,
    /// numbers as people count them (Warcraft II before Warcraft III). Recently added: the newest in the
    /// library's list first, games the library has no entry for after them. Folder size: the largest
    /// on disk first, games that are not on this device after them.
    func sorted(_ games: [BCDGame]) -> [BCDGame] {
        games.sorted { a, b in
            switch self {
            case .played:
                if a.state.isInstalled != b.state.isInstalled { return a.state.isInstalled }
                if a.lastPlayed != b.lastPlayed { return (a.lastPlayed ?? .distantPast) > (b.lastPlayed ?? .distantPast) }
            case .added:
                if a.added != b.added { return (a.added ?? -1) > (b.added ?? -1) }
            case .size:
                let sizeA = a.state.isInstalled ? a.size ?? -1 : -1, sizeB = b.state.isInstalled ? b.size ?? -1 : -1
                if sizeA != sizeB { return sizeA > sizeB }
            case .name:
                break
            }
            let order = a.title.localizedStandardCompare(b.title)
            return order == .orderedSame ? a.id < b.id : order == .orderedAscending
        }
    }
}

enum BCDLibraryFilter: Identifiable, Hashable, CaseIterable {
    case all, installed, store(BCDStoreFront), launcher(String)
    /// The stores' chips, then one chip per launcher (BCDLauncherSpec.shown: Battle.net, EA app; none while they are hidden, build 600).
    static var allCases: [BCDLibraryFilter] {
        [.all, .installed] + BCDStoreFront.allCases.map { .store($0) } + BCDLauncherSpec.shown.map { .launcher($0.id) }
    }
    var id: String { rawValue }
    var rawValue: String {
        switch self {
        case .all: return "all"
        case .installed: return "installed"
        case .store(let s): return s.rawValue
        case .launcher(let id): return "launcher." + id
        }
    }
    var title: String {
        switch self {
        case .all: return "All"
        case .installed: return "Installed"
        case .store(let s): return s.title
        case .launcher(let id): return BCDLauncherSpec.spec(id)?.title ?? id
        }
    }
    var isLauncher: Bool {
        if case .launcher = self { return true }
        return false
    }
}

// Modified 2026-11-04: native search, toolbars and even cover grids for Duo only.
struct BCDDuoLibraryView: View {
    @EnvironmentObject private var store: BCDStore
    @EnvironmentObject private var focus: BCDFocus
    @Environment(\.bcdLayout) private var layout
    /// Y in the shell (BCDFrontEnd): each press opens the search, or closes an open one.
    var searchRequest = 0
    @AppStorage("bcd.library.filter") private var filterKey = BCDLibraryFilter.all.rawValue
    /// The order: BCDLibrarySort's key. Before the sort menu there was only a switch between recent
    /// play and name, kept in `sortByName`; it still decides until a sort is chosen from the menu.
    @AppStorage("bcd.library.sort") private var sortKey = ""
    @AppStorage("bcd.library.sortByName") private var sortByName = false
    @State private var navigationOwner = UUID()
    @State private var searching = false
    @State private var query = ""
    @State private var gridWidth: CGFloat = 0
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var opened: BCDGame?
    @Environment(\.bcdMenu) private var menu

    private var libraryGames: [BCDGame] { store.games.filter { !$0.isWindowsDesktop } }
    private func gameCount(_ store: BCDStoreFront) -> Int { libraryGames.filter { $0.store == store }.count }
    private var filter: BCDLibraryFilter { BCDLibraryFilter.allCases.first { $0.rawValue == filterKey } ?? .all }
    private var sort: BCDLibrarySort { BCDLibrarySort(rawValue: sortKey) ?? (sortByName ? .name : .played) }
    /// The launcher whose chip is chosen (BCDLaunchers.swift); nil for every other filter.
    private var launcher: BCDLauncherSpec? {
        if case .launcher(let id) = filter { return BCDLauncherSpec.spec(id) }
        return nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            filters
            ScrollViewReader { reader in
            ScrollView(.vertical, showsIndicators: false) {
                VStack(alignment: .leading, spacing: 16) {
                    if let launcher { BCDLauncherPanel(spec: launcher) }   // a launcher's chip (Battle.net, EA app): its Install / Open card, no games
                    notices
                    savesNotice
                    LazyVGrid(columns: columns, alignment: .leading, spacing: layout == .pad ? 26 : 20) {
                        ForEach(shown.map { BCDDuoLibraryGame(game: $0) }) { item in
                            let game = item.game
                            BCDCoverCard(game: game, focusID: "library.game.\(game.id)") { opened = game }
                                .id(item.id)

                        }
                    }
                    .bcdGlowLayer()
                    if !query.isEmpty && shown.isEmpty {
                        Text("No game called \"\(query)\" here.").font(BCDStyle.body(15)).foregroundStyle(BCDStyle.textSoft)
                    }
                    if showsDriveHint {
                        Label("To add a game of your own, copy its folder into BCD › wine › drive_c with the Files app, then choose Add a game.",
                              systemImage: "folder.badge.plus")
                            .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(.horizontal, layout.gutter).padding(.top, 14).padding(.bottom, 22)
            }
            .modifier(BCDSoftTopEdge())
            .refreshable { store.actions?.refreshLibrary() }
            .onScrollPhaseChange { _, phase in if phase == .interacting { focus.touched() } }
            .onChange(of: focus.focused) { _, _ in revealGame(reader) }
            .onChange(of: focus.controllerActive) { _, _ in revealGame(reader) }
            .onGeometryChange(for: CGSize.self) { $0.size } action: { _ in revealGame(reader) }
            }
        }
        .padding(.top, 4)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { gridWidth = $0 }
        .searchable(text: $query, isPresented: $searching, prompt: "Find a game")
        .onChange(of: searching) { _, shown in if !shown { query = "" } }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button { store.actions?.addGameFromFiles() } label: { Label("Add a game", systemImage: "plus") }
                    .bcdFocusable("library.add", cornerRadius: 22) { store.actions?.addGameFromFiles() }
                Button { showSortMenu() } label: { Label("Sort by: \(sort.title)", systemImage: "arrow.up.arrow.down") }
                    .bcdFocusable("library.sort", cornerRadius: 22) { showSortMenu() }
            }
            ToolbarItem(placement: .secondaryAction) {
                Button { store.actions?.refreshLibrary() } label: { Label("Refresh library", systemImage: "arrow.clockwise") }
                    .bcdFocusable("library.refresh", cornerRadius: 22) { store.actions?.refreshLibrary() }
            }
        }
        .bcdGamePage($opened, from: "Library")
        // The game menu's "Game page" opens the page over the Library.
        .onAppear { menu?.openPage = { opened = $0 }; installNavigation() }
        .onDisappear { focus.removeNavigator("library.game.", owner: navigationOwner) }
        .onChange(of: shown.map(\.id)) { _, _ in installNavigation() }
        .onChange(of: columns.count) { _, _ in installNavigation() }
        .onChange(of: searchRequest) { _, _ in toggleSearch() }
    }

    /// Ordered movement can reach cards the lazy grid has not created yet.
    private func installNavigation() {
        let ids = shown.map { "library.game." + $0.id }
        let width = columns.count
        focus.setNavigator("library.game.", owner: navigationOwner) { direction in
            guard let current = focus.focused, let index = ids.firstIndex(of: current) else { return false }
            guard let next = BCDGridNavigation.target(index: index, columns: width, count: ids.count,
                                                       direction: direction) else { return false }
            focus.focused = ids[next]
            return true
        }
    }

    private func revealGame(_ reader: ScrollViewProxy) {
        guard focus.controllerActive, let id = focus.focused, id.hasPrefix("library.game.") else { return }
        DispatchQueue.main.async {
            guard focus.controllerActive, focus.focused == id else { return }
            withAnimation(.easeOut(duration: 0.18)) { reader.scrollTo(id, anchor: .center) }
        }
    }

    private func revealFilter(_ reader: ScrollViewProxy) {
        let prefix = "library.filter."
        guard focus.controllerActive, let id = focus.focused, id.hasPrefix(prefix) else { return }
        DispatchQueue.main.async {
            guard focus.controllerActive, focus.focused == id else { return }
            withAnimation(.easeOut(duration: 0.18)) { reader.scrollTo(String(id.dropFirst(prefix.count)), anchor: .center) }
        }
    }

    // MARK: Filters and the row's buttons

    /// Filters scroll independently; search and library actions live in the system toolbar.
    private var filters: some View {
        chips.padding(.trailing, layout.gutter)
    }

    private var chips: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(availableFilters) { item in
                        Button { filterKey = item.rawValue } label: {
                            Text(item.isLauncher ? item.title : "\(item.title) · \(count(item))")
                                .font(BCDStyle.label(14))
                                .padding(.horizontal, 14).frame(minHeight: 44)
                                .foregroundStyle(filter == item ? BCDStyle.text : BCDStyle.textSoft)
                                .bcdGlass(in: Capsule(), tint: filter == item ? BCDStyle.violet : nil, interactive: true)
                        }
                        .buttonStyle(.plain)
                        .bcdFocusable("library.filter.\(item.rawValue)", cornerRadius: 18) { filterKey = item.rawValue }
                        .id(item.rawValue)
                    }
                }
                .padding(.leading, layout.gutter).padding(.trailing, 4).padding(.vertical, 6)
            }
            // The launchers' chips sit at the end of a row that scrolls: a chosen one is brought into view (the other chips stay where they were).
            .onAppear { if filter.isLauncher { proxy.scrollTo(filter.rawValue, anchor: .center) } }
            .onChange(of: filterKey) { _, _ in if filter.isLauncher { proxy.scrollTo(filter.rawValue, anchor: .center) } }
            .onScrollPhaseChange { _, phase in if phase == .interacting { focus.touched() } }
            .onChange(of: focus.focused) { _, _ in revealFilter(proxy) }
            .onChange(of: focus.controllerActive) { _, _ in revealFilter(proxy) }
            .onGeometryChange(for: CGSize.self) { $0.size } action: { _ in revealFilter(proxy) }
        }
    }

    /// Every sort the classic library had, in BCD's own menu (the controller's A on the chip opens it too).
    private func showSortMenu() {
        let current = sort
        menu?.show(BCDMenuContent(title: "Sort by", items: BCDLibrarySort.allCases.map { item in
            BCDMenuItem(id: item.rawValue, title: item.title, symbol: item.symbol, checked: item == current, run: { sortKey = item.rawValue })
        }))
    }

    /// The system search field supports touch, keyboard, VoiceOver, and the controller Y shortcut.
    private func toggleSearch() {
        searching.toggle()
        if !searching { query = "" }
    }

    // MARK: What the library says above and below its games

    @ViewBuilder private var notices: some View {
        let steam = store.accounts.first { $0.store == .steam }
        let steamShown = filter == .all || filter == .store(.steam)
        if steamShown, let steam, steam.available, steam.name == nil {
            Button { store.actions?.signIn(.steam) } label: {
                HStack(spacing: 14) {
                    Image(systemName: "person.crop.circle.badge.plus").font(.system(size: 28, weight: .semibold))
                        .foregroundStyle(BCDStyle.lavender)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Sign in to Steam").font(BCDStyle.heading(17))
                        Text("Your Steam games show up here, and BCD installs them itself.")
                            .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.right").font(.system(size: 13, weight: .bold)).foregroundStyle(BCDStyle.textSoft)
                }
                .padding(16)
                .frame(maxWidth: 620, alignment: .leading)
                .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).strokeBorder(BCDStyle.glow, lineWidth: 1))
            }
            .buttonStyle(.plain)
            .bcdFocusable("library.signin", cornerRadius: 18) { store.actions?.signIn(.steam) }
        } else if steamShown, let steam, steam.name != nil, gameCount(.steam) == 0, query.isEmpty {
            Text("There are no Windows games on this Steam account yet. Refresh reads the account's list again.")
                .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true)
        } else if steamShown, let steam, steam.name != nil, query.isEmpty,
                  store.games.contains(where: { $0.store == .steam }),
                  !store.games.contains(where: { $0.store == .steam && $0.state.isInstalled }) {
            // round 624 (agent N S10, 2FJV7P): a Steam account with games, none installed: how a Steam game starts
            Text("None of your Steam games is installed yet. Tap one, then Install; BCD downloads it from Steam and Play starts it with Valve's own Steam client.")
                .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Games whose Steam Cloud saves differ from this device's, one line each as the classic library's
    /// Steam section said it: nothing is replaced until the player picks the copies to keep on the game's page.
    @ViewBuilder private var savesNotice: some View {
        let steamShown = filter == .all || filter == .store(.steam)
        let games = store.savesDiffer.compactMap { id in store.games.first { $0.id == id } }
            .sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
        if steamShown, query.isEmpty, !games.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(games.prefix(3)) { game in
                    Button { opened = game } label: {
                        HStack(spacing: 12) {
                            Image(systemName: "exclamationmark.icloud").font(.system(size: 20, weight: .semibold))
                                .foregroundStyle(BCDGameColors.amber)
                            Text("Steam Cloud and this device have different saves for \(game.title). Open its page and pick the copy to keep.")
                                .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.text)
                                .multilineTextAlignment(.leading)
                                .fixedSize(horizontal: false, vertical: true)
                            Spacer(minLength: 8)
                            Image(systemName: "chevron.right").font(.system(size: 13, weight: .bold)).foregroundStyle(BCDStyle.textSoft)
                        }
                        .padding(.horizontal, 16).padding(.vertical, 12)
                        .frame(maxWidth: 620, alignment: .leading)
                        .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(BCDGameColors.amber.opacity(0.55), lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    .bcdFocusable("library.saves.\(game.id)", cornerRadius: 16) { opened = game }
                }
                if games.count > 3 {
                    Text("and \(games.count - 3) more").font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                }
            }
        }
    }

    /// No game added by hand yet (the Wine desktop is always there): say how they get in.
    private var showsDriveHint: Bool {
        (filter == .all || filter == .store(.local)) && query.isEmpty && gameCount(.local) == 0
    }

    private var availableFilters: [BCDLibraryFilter] {
        BCDLibraryFilter.allCases.filter { item in
            switch item {
            case .all, .installed: return true
            case .store(let s): return gameCount(s) > 0 || store.accounts.contains { $0.store == s && $0.available }
            case .launcher: return BCDLauncherModel.shared.available
            }
        }
    }

    private func count(_ item: BCDLibraryFilter) -> Int {
        switch item {
        case .all: return libraryGames.count
        case .installed: return libraryGames.filter { $0.state.isInstalled }.count
        case .store(let s): return gameCount(s)
        case .launcher: return 0
        }
    }

    private var shown: [BCDGame] {
        var list = store.games.filter { game in
            guard !game.isWindowsDesktop else { return false }
            switch filter {
            case .all: return true
            case .installed: return game.state.isInstalled
            case .store(let s): return game.store == s
            case .launcher: return false   // a launcher's chip shows its card, not games
            }
        }
        let words = query.trimmingCharacters(in: .whitespaces)
        if !words.isEmpty { list = list.filter { $0.title.localizedCaseInsensitiveContains(words) } }
        return sort.sorted(list)
    }

    // MARK: The grid

    /// Fit covers to the available content width, keeping even columns around the Duo fold.
    private var columns: [GridItem] {
        let gap: CGFloat = layout == .pad ? 20 : 14
        let minimum: CGFloat = typeSize.isAccessibilitySize ? 200 : 120
        let available = max(0, gridWidth - 2 * layout.gutter)
        let fitting = max(2, Int((available + gap) / (minimum + gap)))
        // Even counts keep the fold between covers rather than through one.
        let count = max(2, fitting - fitting % 2)
        return Array(repeating: GridItem(.flexible(), spacing: gap, alignment: .top), count: count)
    }
}

/// The lazy grid can resolve an unrealized controller target from its data identity.
private struct BCDDuoLibraryGame: Identifiable {
    let game: BCDGame
    var id: String { "library.game." + game.id }
}
