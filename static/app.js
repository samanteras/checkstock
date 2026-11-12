const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]);
let catalog = {products: [], warehouses: [], open_operations: []};
let historyCursor = null;

function message(text, error = false) {
  $("message").textContent = text;
  $("message").className = error ? "error" : "";
}

async function request(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  const data = await response.json().catch(() => ({detail: "Сервер вернул некорректный ответ"}));
  if (!response.ok) throw new Error(data.detail || `Ошибка HTTP ${response.status}`);
  return data;
}

function post(path, payload) {
  return request(path, {method: "POST", body: JSON.stringify(payload)});
}

function selectOptions(items, label) {
  return items.map((item) => `<option value="${item.id}">${esc(label(item))}</option>`).join("");
}

function renderCatalog() {
  const form = $("operation-form");
  const prior = Object.fromEntries(["product-select", "source-select", "target-select"].map((id) => [id, $(id).value]));
  $("product-select").innerHTML = selectOptions(catalog.products, (p) => `${p.sku} — ${p.name}`);
  const active = catalog.warehouses.filter((w) => !w.archived_at);
  const all = catalog.warehouses;
  $("source-select").innerHTML = selectOptions(form.elements.namedItem("kind").value === "transfer" ? all : active,
    (w) => `${w.name}${w.archived_at ? " (архив)" : ""}`);
  $("target-select").innerHTML = selectOptions(active, (w) => w.name);
  for (const [id, value] of Object.entries(prior)) if (value && $(id).querySelector(`option[value="${value}"]`)) $(id).value = value;
  $("target-field").hidden = form.elements.namedItem("kind").value !== "transfer";
  $("warehouses").innerHTML = all.map((w) => `<div class="item"><span>${esc(w.name)} ${w.archived_at ? '<span class="badge">архив</span>' : ""}</span><span class="actions"><button class="secondary" data-action="rename" data-id="${w.id}">Переименовать</button>${w.archived_at ? "" : `<button class="danger" data-action="archive" data-id="${w.id}">Удалить</button>`}</span></div>`).join("");
  $("pending").innerHTML = catalog.open_operations.length ? catalog.open_operations.map((o) => {
    const buttons = o.kind === "fbs" ? `<button data-action="ship_fbs" data-id="${o.id}">Отправить FBS</button><button class="secondary" data-action="cancel_fbs" data-id="${o.id}">Отменить резерв</button>` : `<button data-action="receive_${o.kind}" data-id="${o.id}">Принять</button>`;
    return `<div class="item"><span><strong>#${o.id} ${esc(o.sku)}</strong> · ${o.quantity} шт. · ${esc(o.source_name || "")} ${o.target_name ? `→ ${esc(o.target_name)}` : ""}</span><span class="actions">${buttons}</span></div>`;
  }).join("") : '<p class="muted">Нет операций, ожидающих подтверждения.</p>';
}

function renderStock(data) {
  $("balances").innerHTML = data.balances.map((b) => `<tr><td>${esc(b.sku)}</td><td>${esc(b.product_name)}</td><td>${esc(b.warehouse_name)} ${b.archived_at ? '<span class="badge">архив</span>' : ""}</td><td class="num">${b.physical}</td><td class="num">${b.reserved_fbs}</td><td class="num"><strong>${b.available}</strong></td></tr>`).join("");
  const labels = {transfer: "между ФФ", fbo: "на маркетплейс FBO", fbs: "к клиенту FBS"};
  $("transit").innerHTML = data.in_transit.length ? data.in_transit.map((t) => `<tr><td>${esc(t.sku)}</td><td>${esc(t.target_name || labels[t.kind])}</td><td>${esc(t.source_name)}</td><td class="num">${t.quantity}</td><td>${esc(labels[t.kind])}</td></tr>`).join("") : '<tr><td colspan="5" class="muted">Товара в пути нет.</td></tr>';
}

async function loadHistory(reset = true) {
  const data = await request(`/api/history?limit=50${!reset && historyCursor ? `&before_id=${historyCursor}` : ""}`);
  const actionNames = {receipt: "Приход", reserve_fbs: "Резерв FBS", cancel_fbs: "Отмена резерва", ship_fbs: "Отправка FBS", send_transfer: "Отправка между ФФ", receive_transfer: "Приёмка перемещения", send_fbo: "Отправка FBO", receive_fbo: "Приёмка FBO"};
  const rows = data.items.map((e) => `<tr><td>#${e.operation_id} / ${e.id}</td><td>${esc(actionNames[e.action] || e.action)}</td><td>${esc(e.sku)}</td><td class="num">${e.quantity}</td><td>${esc(e.source_name || "—")} ${e.source_archived_at ? '<span class="badge">архив</span>' : ""}</td><td>${esc(e.target_name || "—")} ${e.target_archived_at ? '<span class="badge">архив</span>' : ""}</td></tr>`).join("");
  if (reset) $("history").innerHTML = rows;
  else $("history").insertAdjacentHTML("beforeend", rows);
  historyCursor = data.next_before_id;
  $("more-history").hidden = !historyCursor;
}

async function refresh() {
  const [newCatalog, stock] = await Promise.all([request("/api/catalog"), request("/api/stock")]);
  catalog = newCatalog;
  renderCatalog();
  renderStock(stock);
  await loadHistory();
}

$("operation-form").elements.namedItem("kind").addEventListener("change", renderCatalog);
$("refresh").addEventListener("click", () => refresh().catch((e) => message(e.message, true)));
$("more-history").addEventListener("click", () => loadHistory(false).catch((e) => message(e.message, true)));

$("operation-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  const values = Object.fromEntries(new FormData(form));
  const common = {product_id: Number(values.product_id), quantity: Number(values.quantity), idempotency_key: form.dataset.key || crypto.randomUUID()};
  form.dataset.key = common.idempotency_key;
  let path, body;
  if (values.kind === "transfer") {
    path = "/api/transfers";
    body = {...common, source_warehouse_id: Number(values.warehouse_id), target_warehouse_id: Number(values.target_warehouse_id)};
  } else {
    path = {receipt: "/api/receipts", fbs: "/api/fbs/reservations", fbo: "/api/fbo/shipments"}[values.kind];
    body = {...common, warehouse_id: Number(values.warehouse_id)};
  }
  button.disabled = true;
  try {
    const result = await post(path, body);
    delete form.dataset.key;
    message(`Операция #${result.id} выполнена`);
    await refresh();
  } catch (error) { message(error.message, true); }
  finally { button.disabled = false; }
});

$("warehouse-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await post("/api/warehouses", {name: new FormData(form).get("name")});
    form.reset(); message("Склад добавлен"); await refresh();
  } catch (error) { message(error.message, true); }
});

$("warehouses").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const id = button.dataset.id;
  try {
    if (button.dataset.action === "archive") {
      if (!confirm("Архивировать склад? Остатки и история сохранятся.")) return;
      await request(`/api/warehouses/${id}`, {method: "DELETE"});
      message("Склад архивирован");
    } else {
      const old = catalog.warehouses.find((w) => String(w.id) === id);
      const name = prompt("Новое название склада", old.name);
      if (name === null) return;
      await request(`/api/warehouses/${id}`, {method: "PATCH", body: JSON.stringify({name})});
      message("Склад переименован");
    }
    await refresh();
  } catch (error) { message(error.message, true); }
});

$("pending").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  const id = button.dataset.id;
  const path = action.endsWith("fbs") ? `/api/fbs/reservations/${id}/${action === "ship_fbs" ? "ship" : "cancel"}` : action === "receive_transfer" ? `/api/transfers/${id}/receive` : `/api/fbo/shipments/${id}/receive`;
  button.disabled = true;
  try { await post(path, {}); message(`Операция #${id} подтверждена`); await refresh(); }
  catch (error) { message(error.message, true); button.disabled = false; }
});

refresh().catch((error) => message(`Не удалось загрузить данные: ${error.message}`, true));
