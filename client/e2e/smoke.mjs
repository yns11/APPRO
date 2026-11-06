/**
 * Parcours de non-régression du frontend (audit F32) : lance le serveur sur une base SQLite temporaire avec le jeu de
 * démonstration, construit le client si besoin, puis rejoue au navigateur les défauts reproduits par l'audit :
 *
 *  - F19 : la modification en lot envoie la valeur booléenne affichée (« non » → false) ;
 *  - F20 : le planning de livraisons commence le lundi de la semaine en cours quel que soit le fuseau du navigateur ;
 *  - F22 : une saisie du PDP survit à un rechargement (brouillon), et un enregistrement sur une version périmée est refusé
 *          sans perdre les cellules ;
 *  - F35 : la grille se parcourt au clavier (Tab / flèches / Entrée) et le tiroir rend le focus à son bouton d'ouverture ;
 *  - F10 : un contact transport fournisseur ne voit que l'espace fournisseur.
 *
 *    npm run build && npm run e2e            (depuis client/ ; variables : APPRO_E2E_PORT, APPRO_E2E_TZ)
 *
 * Chromium et Playwright viennent de l'environnement (``playwright`` installé, ou PLAYWRIGHT_MODULE / PLAYWRIGHT_CHROMIUM).
 */
import { spawn } from "node:child_process";
import { mkdtempSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..", "..");
const port = Number(process.env.APPRO_E2E_PORT || 8765);
const tz = process.env.APPRO_E2E_TZ || "Pacific/Auckland";   // a time zone ahead of UTC : the day-picker bugs showed there first
const base = `http://127.0.0.1:${port}`;
const AS_OF = "2026-09-25";                                   // a Friday ; Monday of the week = 2026-09-21

let pw;
try { pw = await import(process.env.PLAYWRIGHT_MODULE || "playwright"); } catch { pw = await import("/opt/node-tools/node_modules/playwright/index.js"); }
const chromium = (pw.default ?? pw).chromium;

if (!existsSync(path.join(here, "..", "dist", "index.html"))) { console.error("client/dist absent : lancer npm run build d'abord"); process.exit(2); }

const work = mkdtempSync(path.join(tmpdir(), "appro-e2e-"));
const server = spawn("python", ["-m", "uvicorn", "appro.api.main:create_app", "--factory", "--port", String(port)], {
  cwd: path.join(root, "backend"), stdio: ["ignore", "pipe", "pipe"],
  env: { ...process.env, APPRO_DB_URL: `sqlite:///${path.join(work, "e2e.db")}`, APPRO_AS_OF: AS_OF, APPRO_DATA_SOURCE: "local", APPRO_LOG_LEVEL: "WARNING", APPRO_TRANSPORT_USERS: "quentin@example.com" },
});
server.stderr.on("data", (d) => { if (process.env.APPRO_E2E_VERBOSE) process.stderr.write(d); });
const stop = () => { try { server.kill("SIGTERM"); } catch { /* already gone */ } };
process.on("exit", stop);

async function waitServer() {
  for (let i = 0; i < 120; i++) {
    try { const r = await fetch(`${base}/api/health`); if (r.ok) return; } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("le serveur ne répond pas");
}
const failures = [];
function check(name, ok, detail = "") { console.log(`${ok ? "OK  " : "FAIL"} ${name}${detail ? ` – ${detail}` : ""}`); if (!ok) failures.push(name); }

await waitServer();
const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM || undefined, args: ["--no-sandbox"] });
const ctx = await browser.newContext({ timezoneId: tz, locale: "fr-FR", extraHTTPHeaders: { "x-forwarded-email": "quentin@example.com" }, viewport: { width: 1500, height: 950 } });
const page = await ctx.newPage();
page.on("pageerror", (e) => failures.push(`erreur de page : ${e.message}`));

try {
  // ---- F20 : the delivery planning opens on the Monday of the current week, in a far time zone
  await page.goto(`${base}/livraisons`, { waitUntil: "networkidle" });
  const planningStart = await page.evaluate(async () => (await (await fetch("/api/transports/planning")).json()).start);
  const firstHead = await page.locator(".tp-grid .tp-hd").nth(1).innerText().catch(() => "");
  check("F20 planning : lundi de la semaine en cours côté serveur", planningStart === "2026-09-21", planningStart);
  check("F20 planning : première colonne = lundi 21/09 dans le fuseau " + tz, /21\/09/.test(firstHead), firstHead);

  // ---- F22 : PDP draft survives a reload, stale save refused and cells kept
  await page.goto(`${base}/pdp`, { waitUntil: "networkidle" });
  const sheet = await page.evaluate(async () => (await (await fetch("/api/pdp/sheet?planner=QUENTIN")).json()));   // the page filters on the default perimeter
  const prog = sheet.programs.find((p) => p.has_bom);
  const colIdx = sheet.weeks.findIndex((w) => w.editable && !w.past);
  const uiCol = colIdx - sheet.weeks.filter((w) => w.past).length;     // the sheet opens on the current week : the past weeks are hidden
  const rowIdx = sheet.programs.indexOf(prog);
  const cell = page.locator(".pdp-sheet tbody tr").nth(rowIdx).locator("td.num").nth(uiCol);
  await cell.click();                      // select, then a digit starts the entry with that digit (the old value is replaced)
  await page.keyboard.type("4321");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(200);
  check("F22 PDP : cellule saisie en attente (bouton Enregistrer (1))", await page.getByRole("button", { name: /Enregistrer \(1\)/ }).isVisible());
  await page.reload({ waitUntil: "networkidle" });
  check("F22 PDP : brouillon restauré après rechargement", await page.getByRole("button", { name: /Enregistrer \(1\)/ }).isVisible({ timeout: 5000 }).catch(() => false));
  // a colleague saves in between : the active version changes
  const stale = await page.evaluate(async ({ pid, week }) => {
    const r = await fetch("/api/pdp/sheet", { method: "PUT", headers: { "Content-Type": "application/json", "x-forwarded-email": "collegue@example.com" }, body: JSON.stringify({ cells: [{ program_id: pid, week_start: week, qty: 777 }] }) });
    return r.status;
  }, { pid: prog.program_id, week: sheet.weeks[colIdx].week_start });
  check("F22 PDP : enregistrement du collègue accepté (201)", stale === 201, String(stale));
  const refused = page.waitForResponse((r) => r.url().includes("/api/pdp/sheet") && r.request().method() === "PUT");
  await page.getByRole("button", { name: /Enregistrer \(1\)/ }).click();
  check("F22 PDP : enregistrement sur une version périmée refusé (409)", (await refused).status() === 409);
  const banner = page.locator(".banner[role=alert]");
  await banner.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  check("F22 PDP : conflit affiché, saisie conservée", await banner.isVisible() && await page.getByRole("button", { name: /Enregistrer \(1\)/ }).isVisible());
  await page.getByRole("button", { name: "Recharger en conservant mes saisies" }).click();
  await banner.waitFor({ state: "hidden", timeout: 10000 }).catch(() => {});
  const accepted = page.waitForResponse((r) => r.url().includes("/api/pdp/sheet") && r.request().method() === "PUT");
  await page.getByRole("button", { name: /Enregistrer \(1\)/ }).click();
  const acc = await accepted;
  if (process.env.APPRO_E2E_VERBOSE) console.log("PUT body", acc.request().postData(), "->", acc.status(), await acc.text());
  check("F22 PDP : enregistrement accepté sur la nouvelle base (201)", acc.status() === 201);
  await page.waitForTimeout(300);
  const after = await page.evaluate(async () => (await (await fetch("/api/pdp/sheet?planner=QUENTIN")).json()));
  const saved = after.programs.find((p) => p.program_id === prog.program_id).values[colIdx];
  check("F22 PDP : enregistrement réussi après rechargement de la base", saved === 4321, String(saved));

  // ---- F35 : keyboard on the supply table, Drawer focus restitution
  await page.goto(`${base}/tableau`, { waitUntil: "networkidle" });
  await page.waitForSelector(".pivot td[data-cell^='plan|']", { timeout: 15000 });
  const first = page.locator(".pivot td[data-cell^='plan|'][tabindex='0']").first();
  await first.focus();
  const ref0 = await page.evaluate(() => document.activeElement?.getAttribute("data-cell"));
  await page.keyboard.press("ArrowRight");
  const ref1 = await page.evaluate(() => document.activeElement?.getAttribute("data-cell"));
  check("F35 grille : flèche droite déplace le focus de cellule", !!ref0 && !!ref1 && ref0 !== ref1 && ref1.startsWith("plan|"), `${ref0} → ${ref1}`);
  await page.keyboard.press("Enter");
  check("F35 grille : Entrée ouvre la saisie", await page.locator(".pivot .cell-input").isVisible());
  await page.keyboard.press("Escape");
  const refBack = await page.evaluate(() => document.activeElement?.getAttribute("data-cell"));
  check("F35 grille : Échap rend le focus à la cellule", refBack === ref1, String(refBack));
  await page.goto(`${base}/referentiel?table=ref_suppliers`, { waitUntil: "networkidle" });
  const add = page.getByRole("button", { name: "Ajouter" });
  await add.focus();
  await add.click();
  const dialog = page.getByRole("dialog");
  check("F35 tiroir : focus placé dans le dialogue", await dialog.evaluate((el) => el.contains(document.activeElement)));
  await page.keyboard.press("Escape");
  await page.waitForTimeout(100);
  check("F35 tiroir : focus rendu au bouton d'ouverture", await page.evaluate(() => document.activeElement?.textContent?.includes("Ajouter")));

  // ---- F19 : bulk edit of a boolean sends the displayed value
  const sent = [];
  await page.route("**/api/reference/ref_suppliers/rows", async (route) => { sent.push(JSON.parse(route.request().postData() || "{}")); await route.continue(); });
  await page.getByRole("button", { name: "Modifier en lot" }).click();
  await page.getByLabel("Colonne").selectOption("strict_days");
  await page.getByLabel("Valeur").selectOption("non");
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: /Appliquer à/ }).click();
  await page.waitForTimeout(1500);
  check("F19 lot : la valeur booléenne envoyée est « non » (false) avec le tampon de la ligne", sent.length > 0 && sent.every((b) => b.values.strict_days === false && b.expected_updated_at), `${sent.length} requête(s)`);

  // ---- F10 : a supplier contact only sees the portal
  const sup = await browser.newContext({ extraHTTPHeaders: { "x-forwarded-email": "transport@fournisseur.example" } });
  await page.evaluate(async () => {
    const rows = await (await fetch("/api/reference/ref_suppliers/rows")).json();
    const r = rows.find((x) => x.supplier_id === "S-000378");
    const values = Object.fromEntries(Object.entries(r).filter(([k]) => !["updated_by", "updated_at", "articles", "article_count"].includes(k)));
    await fetch("/api/reference/ref_suppliers/rows", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values: { ...values, transport_contact: "transport@fournisseur.example" } }) });
  });
  const sp = await sup.newPage();
  await sp.goto(`${base}/`, { waitUntil: "networkidle" });
  check("F10 fournisseur : redirigé vers l'espace fournisseur", sp.url().endsWith("/portail"), sp.url());
  const forbidden = await sp.evaluate(async () => (await fetch("/api/cockpit")).status);
  check("F10 fournisseur : /api/cockpit répond 403", forbidden === 403, String(forbidden));
  await sup.close();
} catch (e) {
  failures.push(`exception : ${e.message}`);
  console.error(e);
} finally {
  await browser.close();
  stop();
}
console.log(failures.length ? `\n${failures.length} échec(s) : ${failures.join(" ; ")}` : "\nParcours complet : aucun échec");
process.exit(failures.length ? 1 : 0);
