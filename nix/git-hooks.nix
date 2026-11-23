{ lib, pkgs }:
{
  src = ../.;
  package = pkgs.prek;
  default_stages = [ "pre-commit" ];

  hooks = {
    betterleaks = {
      enable = true;
      name = "betterleaks (staged)";
      entry = "${lib.getExe pkgs.betterleaks} git --pre-commit --redact --staged --verbose";
      pass_filenames = false;
    };
    betterleaks-history = {
      enable = true;
      name = "betterleaks (full history)";
      entry = "${lib.getExe pkgs.betterleaks} git --redact --verbose";
      pass_filenames = false;
      always_run = true;
      stages = [ "pre-push" ];
    };
    forbid-local-state = {
      enable = true;
      name = "forbid local agent state and credentials";
      entry = "${pkgs.writeShellScript "forbid-local-state" ''
        printf 'never commit local agent state or credentials: %s\n' "$@"
        exit 1
      ''}";
      files = "(^|/)(ledger\\.(json|tmp|lock)|gh-token|linear-client|\\.env(\\..+)?|.+\\.lock)$|^work/";
      excludes = [ "^flake\\.lock$" ];
    };
    check-added-large-files.enable = true;
    check-case-conflicts.enable = true;
    check-executables-have-shebangs.enable = true;
    check-json.enable = true;
    check-merge-conflicts.enable = true;
    check-python.enable = true;
    check-shebang-scripts-are-executable.enable = true;
    check-symlinks.enable = true;
    codespell = {
      enable = true;
      name = "codespell";
      entry = "${lib.getExe pkgs.codespell} --write-changes";
      types = [ "text" ];
      excludes = [
        "\\.svg$"
        "^flake\\.lock$"
      ];
    };
    commitizen.enable = true;
    deadnix.enable = true;
    detect-private-keys.enable = true;
    end-of-file-fixer.enable = true;
    fix-byte-order-marker.enable = true;
    mixed-line-endings = {
      enable = true;
      args = [ "--fix=auto" ];
    };
    nixfmt.enable = true;
    prettier = {
      enable = true;
      types_or = [
        "markdown"
        "json"
      ];
      settings.prose-wrap = "always";
    };
    python-debug-statements.enable = true;
    ruff = {
      enable = true;
      args = [ "--exit-non-zero-on-fix" ];
    };
    ruff-format.enable = true;
    statix = {
      enable = true;
      entry = "${lib.getExe pkgs.statix} fix";
    };
    trim-trailing-whitespace.enable = true;
  };
}
