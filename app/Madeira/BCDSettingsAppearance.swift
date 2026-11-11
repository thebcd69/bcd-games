// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// Copyright 2026 creeper4004

import SwiftUI
import UniformTypeIdentifiers

struct BCDAppearanceSettings: View {
    private let themes = BCDThemeStore.shared
    @EnvironmentObject private var model: BCDSettingsModel
    @State private var choosingColor = false
    @State private var naming = false
    @State private var profileName = ""
    @State private var importing = false
    @State private var exporting = false
    @State private var document: BCDThemeDocument?
    @State private var errorMessage: String?

    private var mainColor: Binding<Color> {
        Binding(get: { (BCDThemeRGB(hex: themes.current.seed ?? "") ?? themes.current.rgb("accent")).color },
                set: { themes.setMainColor($0) })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            BCDSettingsGroup(title: "Color profile", footer: "Colors change immediately throughout BCD and are remembered on this device.") {
                BCDSettingsValueRow(id: id("profile"), title: "Profile",
                                    choices: themes.profiles.map { BCDSettingsChoice(value: $0.id, label: $0.name) },
                                    value: themes.current.id, set: { themes.select($0) })
                preview.padding(14)
            }
            BCDSettingsGroup(title: "Make it yours", footer: "Pick a main color to create a matching palette. BCD adjusts the surfaces and action colors to keep text readable. Save a copy to keep several color sets.") {
                Button { choosingColor = true } label: {
                    BCDSettingsRowLabel(title: "Main color", note: nil) {
                        HStack(spacing: 8) {
                            Text(themes.current.seed ?? themes.current.colors["accent"] ?? "")
                                .font(BCDStyle.label(14)).foregroundStyle(BCDStyle.textSoft)
                            Circle().fill(mainColor.wrappedValue).frame(width: 25, height: 25)
                                .overlay(Circle().strokeBorder(BCDStyle.textSoft.opacity(0.5), lineWidth: 1))
                        }
                    }
                }
                .buttonStyle(.plain)
                .bcdSettingsControl(id("color")) { choosingColor = true }
                BCDSettingsActionRow(id: id("save"), title: "Save as a new profile", symbol: "plus.circle") {
                    profileName = themes.current.id == "custom" ? "My colors" : themes.current.name + " copy"
                    naming = true
                }
            }
            BCDSettingsGroup(title: "Your profiles", footer: "Import or share a BCD color profile using Files.") {
                BCDSettingsActionRow(id: id("import"), title: "Import a profile", symbol: "square.and.arrow.down") { importing = true }
                BCDSettingsActionRow(id: id("export"), title: "Export this profile", symbol: "square.and.arrow.up") {
                    do { document = try BCDThemeDocument(profile: themes.current); exporting = true }
                    catch { errorMessage = error.localizedDescription }
                }
                if themes.canDelete {
                    BCDSettingsActionRow(id: id("delete"), title: "Delete this profile", symbol: "trash", destructive: true) {
                        model.ask(BCDSettingsConfirm(title: "Delete \(themes.current.name)?", message: "BCD Violet will be selected. Your other profiles will stay available.",
                                                    choices: [.init(title: "Delete profile", destructive: true, run: { themes.deleteCurrent() })]))
                    }
                }
                BCDSettingsActionRow(id: id("reset"), title: "Use BCD Violet", symbol: "arrow.counterclockwise") { themes.select("bcd-violet") }
            }
            if let error = themes.storageError { BCDSettingsNotice(text: error) }
        }
        .sheet(isPresented: $choosingColor, onDismiss: { model.cover(nil) }) {
            VStack(alignment: .leading, spacing: 20) {
                HStack {
                    Text("Main color").font(BCDStyle.heading(22))
                    Spacer()
                    Button("Done") { choosingColor = false }.font(BCDStyle.label(16))
                }
                ColorPicker("Choose a color", selection: mainColor, supportsOpacity: false)
                preview
            }
            .padding(24).foregroundStyle(BCDStyle.text).tint(BCDStyle.lavender)
            .presentationDetents([.medium]).presentationDragIndicator(.visible)
            .presentationBackground(BCDStyle.surface).preferredColorScheme(.dark)
        }
        .onChange(of: choosingColor) { _, shown in if shown { model.cover { choosingColor = false } } }
        .alert("Save color profile", isPresented: $naming) {
            TextField("Profile name", text: $profileName)
            Button("Save") { do { try themes.saveAs(profileName) } catch { errorMessage = error.localizedDescription } }
                .disabled(profileName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || profileName.count > 60)
            Button("Cancel", role: .cancel) {}
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.json]) { result in
            do { try themes.importProfile(result.get()) } catch { errorMessage = error.localizedDescription }
        }
        .fileExporter(isPresented: $exporting, document: document, contentType: .json, defaultFilename: "BCD-" + themes.current.id) { result in
            if case .failure(let error) = result { errorMessage = error.localizedDescription }
        }
        .alert("Color profile", isPresented: Binding(get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } })) {
            Button("OK") { errorMessage = nil }
        } message: { Text(errorMessage ?? "") }
    }

    private func id(_ name: String) -> String { BCDSettingsID.control(.appearance, name) }

    private var preview: some View {
        VStack(alignment: .leading, spacing: 13) {
            HStack(spacing: 10) {
                Image(systemName: "paintpalette.fill").foregroundStyle(BCDStyle.lavender)
                Text(themes.current.name).font(BCDStyle.heading(18))
                Spacer(minLength: 0)
                HStack(spacing: 5) {
                    ForEach(["accent", "accentSecondary", "accentSoft"], id: \.self) { role in
                        Circle().fill(themes.color(role)).frame(width: 14, height: 14)
                    }
                }.accessibilityHidden(true)
            }
            Text("Your library, your colors.").font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
            HStack(spacing: 12) {
                Label("Play", systemImage: "play.fill").font(BCDStyle.label(15))
                    .foregroundStyle(BCDStyle.onAccent).padding(.horizontal, 22).padding(.vertical, 10)
                    .background(BCDStyle.glow, in: Capsule())
                Label("Ready", systemImage: "checkmark.circle.fill").font(BCDStyle.label(14)).foregroundStyle(BCDStyle.mint)
            }
        }
        .padding(18).frame(maxWidth: .infinity, alignment: .leading)
        .background(BCDStyle.backdrop, in: RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(BCDStyle.stroke, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }
}

struct BCDThemeDocument: FileDocument {
    static var readableContentTypes: [UTType] { [.json] }
    var data: Data
    init(profile: BCDThemeProfile) throws { data = try profile.encoded() }
    init(configuration: ReadConfiguration) throws {
        let contents = configuration.file.regularFileContents ?? Data()
        data = try BCDThemeProfile.decode(contents).encoded()
    }
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper { FileWrapper(regularFileWithContents: data) }
}
