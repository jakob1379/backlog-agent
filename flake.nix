{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    git-hooks = {
      url = "github:cachix/git-hooks.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      git-hooks,
      ...
    }:
    let
      forAll =
        f:
        nixpkgs.lib.genAttrs [
          "x86_64-linux"
          "aarch64-linux"
        ] (system: f nixpkgs.legacyPackages.${system});
    in
    {
      checks = forAll (pkgs: {
        pre-commit-check = git-hooks.lib.${pkgs.stdenv.hostPlatform.system}.run (
          import ./nix/git-hooks.nix {
            inherit pkgs;
            inherit (nixpkgs) lib;
          }
        );
      });

      formatter = forAll (
        pkgs:
        let
          inherit (self.checks.${pkgs.stdenv.hostPlatform.system}.pre-commit-check.config) configFile package;
        in
        pkgs.writeShellScriptBin "prek-fmt" ''
          ${pkgs.lib.getExe package} run --all-files --config ${configFile}
        ''
      );

      devShells = forAll (pkgs: {
        default =
          let
            inherit (self.checks.${pkgs.stdenv.hostPlatform.system}) pre-commit-check;
            inherit (pre-commit-check.config) configFile installStages package;
            git = pkgs.lib.getExe pkgs.git;
            prek = pkgs.lib.getExe package;
            hookStages = pkgs.lib.concatStringsSep " " (pkgs.lib.remove "manual" installStages);
          in
          pkgs.mkShell {
            packages = [
              pkgs.python3
              pkgs.gh
              pkgs.git
            ]
            ++ pre-commit-check.enabledPackages;
            # Hooks go in the common git dir so every worktree, and the agent's own
            # commits on main, run them.
            shellHook = ''
              ${pre-commit-check.shellHook}
              common_dir=$(${git} rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
              if [ -n "$common_dir" ] && {
                [ "$(${git} config --local core.hooksPath)" != "$common_dir/hooks" ] \
                  || ! grep -qF -- '--config="${configFile}"' "$common_dir/hooks/pre-commit" 2>/dev/null
              }; then
                ${git} config --local --unset-all core.hooksPath || true
                for stage in ${hookStages}; do
                  ${prek} install -f -c ${configFile} -t "$stage"
                done
                ${git} config --local core.hooksPath "$common_dir/hooks"
              fi
            '';
          };
      });
    };
}
