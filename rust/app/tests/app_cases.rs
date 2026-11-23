#[test]
fn recorded_cases() {
    trycmd::TestCases::new()
        .register_bin(
            "gossamer",
            std::path::Path::new(env!("CARGO_BIN_EXE_gossamer")),
        )
        .case("../app-cases/*.toml")
        .skip("../app-cases/audit-cargo-empty-lock.toml")
        .skip("../app-cases/cache-prune.toml")
        .skip("../app-cases/skills-install-duplicate-skill.toml")
        .skip("../app-cases/skills-show.toml")
        .skip("../app-cases/verify-bundled-assets-bad-whl.toml")
        .skip("../app-cases/verify-bundled-assets-good-targz.toml")
        .skip("../app-cases/verify-bundled-assets-good-whl.toml")
        .skip("../app-cases/verify-bundled-assets-missing-whl.toml")
        .skip("../app-cases/verify-bundled-assets-mixed-unsupported.toml");
}
