/* DELEGATE WORK in VS Code, Cursor, VSCodium and Windsurf (platform_layer/
   editor_menus.py; the user, 2026-11-01: "所有text...都是这个形式，只有直接在网址
   或者文件里才是右键delegate work"):
   - TEXT SELECTED: the editor's own light bulb beside it opens its menu -
     Delegate work, Learn - with the text, the file, the line and the lines
     around it;
   - THE FILE: Delegate work on the right-click menu of the editor, its tab
     and the file explorer - the file itself.
   What was picked goes to the agent on this computer (`--delegate-picked -` /
   `--learn-picked -`, on its standard input), which opens its box on it.
   Nothing else is read, and nothing is sent anywhere else. */

const vscode = require("vscode");
const childProcess = require("child_process");
const fs = require("fs");
const path = require("path");

const AROUND_LINES = 6, PAGE = 8000;

function agentCommand() {
  return JSON.parse(fs.readFileSync(path.join(__dirname, "agent.json"), "utf8")).command;
}

function handOver(picked, flag) {
  const [program, ...args] = agentCommand();
  const child = childProcess.spawn(program, [...args, flag || "--delegate-picked", "-"], {
    detached: true, stdio: ["pipe", "ignore", "ignore"], windowsHide: true,
  });
  child.on("error", (err) => vscode.window.showErrorMessage(
    `Delegate work: the agent could not be started (${err.message}).`));
  child.stdin.end(JSON.stringify(picked));
  child.unref();
}

function theFile(uri) {
  return { where: "vscode", app: vscode.env.appName, kind: "file", file: uri.fsPath };
}

function theSelection(doc, range) {
  const text = doc.getText(range);
  const first = Math.max(0, range.start.line - AROUND_LINES);
  const last = Math.min(doc.lineCount - 1, range.end.line + AROUND_LINES);
  return {
    where: "vscode", app: vscode.env.appName, kind: "selection",
    file: doc.uri.scheme === "file" ? doc.uri.fsPath : "",
    url: doc.uri.scheme === "file" ? "" : doc.uri.toString(),
    title: path.basename(doc.fileName || ""), line: range.start.line + 1, text,
    around: doc.getText(new vscode.Range(first, 0, last, doc.lineAt(last).text.length)),
    page: doc.getText().slice(0, PAGE),
  };
}

/* The light bulb beside selected text: its two actions. */
class AtTheSelection {
  provideCodeActions(doc, range) {
    if (range.isEmpty || !doc.getText(range).trim()) return [];
    const picked = theSelection(doc, range);
    return [["Delegate work", "--delegate-picked"], ["Learn", "--learn-picked"]].map(
      ([title, flag]) => {
        const action = new vscode.CodeAction(title, vscode.CodeActionKind.Empty);
        action.command = { title, command: "personalAgent.handOver", arguments: [picked, flag] };
        return action;
      });
  }
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("personalAgent.handOver",
      (picked, flag) => handOver(picked, flag)),
    vscode.languages.registerCodeActionsProvider({ scheme: "*" }, new AtTheSelection(),
      { providedCodeActionKinds: [vscode.CodeActionKind.Empty] }),
    vscode.commands.registerCommand("personalAgent.delegateWork", (uri, chosen) => {
      const many = Array.isArray(chosen) && chosen.length > 1 ? chosen : null;
      for (const one of many || [uri || (vscode.window.activeTextEditor
        && vscode.window.activeTextEditor.document.uri)]) {
        if (one && one.scheme === "file") handOver(theFile(one));
      }
    }));
}

function deactivate() {}

module.exports = { activate, deactivate };
