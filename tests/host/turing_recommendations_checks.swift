// SPDX-License-Identifier: GPL-3.0-only
import Foundation

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    guard condition() else { fatalError(message) }
}
let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let file = try RecommendationsFile.parse(data)
let rule = file.rules.first { $0.id == "turing-complete" }!
let firstBuild = rule.minBuild!
struct CatalogEntry: Decodable { let key: String; let category: String }
let metadata = try JSONDecoder().decode([CatalogEntry].self,
    from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[2])))
let catalog = metadata.map { (key: $0.key, category: $0.category) }
let policy = RecommendationsPolicy(catalog: catalog)
try file.checkPolicy(policy, build: firstBuild, everyLine: true)
let current = file.list(build: firstBuild, origin: .builtIn)
let found = current.match(windowsPath: #"C:\Games\Turing Complete"#, steamAppID: 1444480, fileExists: { _ in false })
require(found?.id == "turing-complete", "Steam App ID must select the complete preset")
require(found?.fileText == rule.config.map(\.text).joined(separator: "\n") + "\n", "all seven settings reach the game")
require(current.match(windowsPath: #"C:\Games\Unrelated"#, steamAppID: 99999999, fileExists: { _ in false }) == nil,
        "unrelated games must not receive the workaround")
let oldPolicy = RecommendationsPolicy(catalog: catalog,
                                    envNames: RecommendationsPolicy.allowedEnvNames.subtracting(["FEX_SMCCHECKS", "MADEIRA_FEX_EXEC_RECHECK"]))
try file.checkPolicy(oldPolicy, build: firstBuild - 1)
for build in [nil, 712, firstBuild - 1] as [Int?] {
    let old = file.list(build: build, origin: .builtIn)
    require(old.match(windowsPath: #"C:\Games\Turing Complete"#, steamAppID: 1444480, fileExists: { _ in false }) == nil,
            "older/unknown builds must skip the unsupported profile")
}
func accepts(_ text: String) -> Bool {
    do { try policy.check(RecommendationLine(text: text), at: "test"); return true }
    catch { return false }
}
for good in ["env.FEX_SMCCHECKS = full", "env.MADEIRA_FEX_EXEC_RECHECK = 0", "env.MADEIRA_FEX_EXEC_RECHECK = 1",
             "env.MESA_GL_VERSION_OVERRIDE = 4.1", "env.MESA_GL_VERSION_OVERRIDE = 4.6",
             "env.MESA_GLSL_VERSION_OVERRIDE = 410", "env.MESA_GLSL_VERSION_OVERRIDE = 460"] {
    require(accepts(good), "rejects supported value: \(good)")
}
for bad in ["env.FEX_SMCCHECKS = none", "env.FEX_SMCCHECKS = arbitrary", "env.MADEIRA_FEX_EXEC_RECHECK = yes",
            "env.MADEIRA_FEX_EXEC_RECHECK = 2", "env.MESA_GL_VERSION_OVERRIDE = 4.0", "env.MESA_GL_VERSION_OVERRIDE = 4.7",
            "env.MESA_GLSL_VERSION_OVERRIDE = 400", "env.MESA_GLSL_VERSION_OVERRIDE = 470",
            "env.MADEIRA_UNKNOWN_ENV = 1"] {
    require(!accepts(bad), "accepts unsupported value: \(bad)")
}
print("PASS: exact seven-line profile, App ID matching, first/older/unknown build gates and strict allowed values")
