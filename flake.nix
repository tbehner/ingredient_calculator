{
  description = "ingredients-calculator — Flask frontend over an SQLite ingredients database";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    devshell = {
      url = "github:numtide/devshell";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      devshell,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [ devshell.overlays.default ];
        };

        python = pkgs.python312;
      in
      {
        devShells.default = pkgs.devshell.mkShell {
          name = "ingredients-calculator";

          packages = [
            python
            pkgs.uv
            pkgs.ruff
            pkgs.sqlite
            pkgs.sqlitebrowser
          ];

          # `eval` rather than `value`: devshell shell-escapes `value`, so
          # $PRJ_ROOT would be exported literally instead of expanded.
          env = [
            {
              name = "PYTHONPATH";
              eval = "$PRJ_ROOT/src";
            }
            {
              # Keep uv-managed venvs local to the project.
              name = "UV_PROJECT_ENVIRONMENT";
              eval = "$PRJ_ROOT/.venv";
            }
            {
              name = "INGREDIENTS_DB";
              eval = "$PRJ_ROOT/ingredients.sqlite3";
            }
          ];

          commands = [
            {
              name = "setup";
              help = "Create the virtualenv and install dependencies";
              command = "uv sync";
            }
            {
              name = "serve";
              help = "Run the Flask development server";
              command = ''
                uv run flask --app ingredients_calculator.app:create_app \
                  run --debug "$@"
              '';
            }
            {
              name = "db";
              help = "Open the ingredients database in the sqlite3 shell";
              command = "sqlite3 \"$INGREDIENTS_DB\" \"$@\"";
            }
            {
              # Not "test": that is a shell builtin and would shadow this script.
              name = "check";
              help = "Run the test suite";
              command = "uv run pytest \"$@\"";
            }
            {
              name = "lint";
              help = "Lint and format with ruff";
              command = "ruff check . && ruff format --check .";
            }
          ];

          devshell.startup.venv.text = ''
            if [ ! -d "$PRJ_ROOT/.venv" ]; then
              echo "No .venv found — run 'setup' to create it."
            fi
          '';
        };
      }
    );
}
