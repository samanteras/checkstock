# Repository Guidelines

## Project Structure & Module Organization

This directory is currently an empty project scaffold. As code is added, keep application source in `src/`, automated tests in `tests/` (or beside the code they cover), and static assets in `public/` or `assets/`. Put setup and architectural notes in `docs/`. Add a root `README.md` that maps the actual layout once the first application is committed.

## Build, Test, and Development Commands

No build system, package manifest, or runnable commands are present yet. When introducing one, define and document a small, consistent command set for local development, testing, linting, and production builds. For example, a Node.js project might expose `npm run dev`, `npm test`, `npm run lint`, and `npm run build` in `package.json`. Keep the README and this guide synchronized with the commands that actually work.

## Coding Style & Naming Conventions

Follow the formatter and linter chosen for the first implementation; commit their configuration so everyone gets the same results. Until then, use consistent indentation within each file, descriptive names, and one naming pattern per language. Prefer `PascalCase` for components and types, `camelCase` for JavaScript or TypeScript functions and variables, and `snake_case` for Python functions and modules if those languages are adopted.

## Testing Guidelines

There is no test framework or coverage target yet. Add focused tests with each behavior change and keep fixtures small and deterministic. Name tests for the behavior they check, such as `creates_order.test.ts` or `test_creates_order.py`. Document the test command and any required services or environment variables before relying on tests as a contribution gate.

## Commit & Pull Request Guidelines

No Git history exists here, so there is no established commit convention. Use short, imperative subjects such as `Add order validation`, and keep unrelated changes in separate commits. Pull requests should explain the change, how it was verified, and any setup or migration steps. Link related issues when available; include screenshots for visible interface changes.
