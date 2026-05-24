// Mirrors the conventional-pre-commit gate so frontend devs without the
// Python pre-commit framework still get conventional-commit validation
// via Husky's commit-msg hook.
module.exports = {
  extends: ['@commitlint/config-conventional'],
};
