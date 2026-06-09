/* AI Token dashboard — vanilla JS, polls the local node's REST API. */

const COIN = 1e8;
const $ = (id) => document.getElementById(id);
const fmtAit = (u) => (u / COIN).toLocaleString(undefined, { maximumFractionDigits: 8 });
const fmtUsd = (c) => "$" + (c / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const short = (h) => (h && h.length > 16 ? h.slice(0, 10) + "…" + h.slice(-6) : h || "");
const ago = (ts) => {
  const s = Math.max(0, (Date.now() / 1000) - ts);
  if (s < 60) return Math.floor(s) + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  return Math.floor(s / 3600) + "h ago";
};
const fmtHashrate = (h) => {
  if (h > 1e9) return (h / 1e9).toFixed(1) + " G";
  if (h > 1e6) return (h / 1e6).toFixed(1) + " M";
  if (h > 1e3) return (h / 1e3).toFixed(1) + " k";
  return h.toFixed(0) + " ";
};

async function api(path, opts) {
  const resp = await fetch(path, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || resp.statusText);
  return data;
}
const post = (path, body, method = "POST") =>
  api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function setMsg(id, text, ok) {
  const el = $(id);
  el.textContent = text;
  el.className = "msg " + (ok ? "ok" : "err");
  if (ok) setTimeout(() => { el.textContent = ""; el.className = "msg"; }, 6000);
}

/* ------------------------------------------------------------- tabs */
document.querySelectorAll("#tabs button").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    btn.classList.add("active");
    $("tab-" + btn.dataset.tab).classList.add("active");
    refresh();
  };
});
const activeTab = () => document.querySelector("#tabs button.active").dataset.tab;

/* ----------------------------------------------------------- wallet */
const wallet = () => JSON.parse(localStorage.getItem("ait_wallet") || "null");

async function createWallet() {
  const w = await api("/api/wallet/new", { method: "POST" });
  localStorage.setItem("ait_wallet", JSON.stringify(w));
  refresh();
}
function forgetWallet() {
  if (confirm("Forget this wallet? Its private key will be gone from this browser.")) {
    localStorage.removeItem("ait_wallet");
    refresh();
  }
}
const copyAddress = () => navigator.clipboard.writeText(wallet().address);
function copyMineCmd() { navigator.clipboard.writeText($("mine-cmd").textContent); }

async function sendAit() {
  const w = wallet();
  if (!w) return setMsg("send-msg", "create a wallet first");
  try {
    const r = await post("/api/wallet/sign-and-send", {
      private_key: w.private_key,
      recipient: $("send-to").value.trim(),
      amount: Math.round(parseFloat($("send-amount").value) * COIN),
      fee: 0,
    });
    setMsg("send-msg", "sent, pending in block: " + short(r.txid), true);
  } catch (e) { setMsg("send-msg", e.message); }
}

/* --------------------------------------------------------- explorer */
async function explorerSearch() {
  const q = $("explorer-search").value.trim();
  if (!q) return;
  const box = $("explorer-result"), pre = $("explorer-result-pre");
  box.classList.remove("hidden");
  for (const path of [`/api/block/${q}`, `/api/tx/${q}`, `/api/address/${q}`]) {
    try { pre.textContent = JSON.stringify(await api(path), null, 2); return; } catch {}
  }
  pre.textContent = "no block, transaction or address found for: " + q;
}

function renderBlocks(data) {
  $("blocks-table").querySelector("tbody").innerHTML = data.blocks.map((b) => `
    <tr>
      <td>${b.index}</td>
      <td class="hash" onclick="$('explorer-search').value='${b.hash}';explorerSearch()">${short(b.hash)}</td>
      <td>${ago(b.timestamp)}</td>
      <td>${b.transactions.length}</td>
      <td>${b.transactions[0] ? fmtAit(b.transactions[0].amount) + " AIT" : "–"}</td>
      <td>${b.difficulty_bits} bits</td>
    </tr>`).join("");
}

/* ------------------------------------------------------------ trade */
function requireWallet(msgId) {
  const w = wallet();
  if (!w) { setMsg(msgId, "create a wallet first (Wallet tab)"); return null; }
  return w;
}

async function faucet() {
  const w = requireWallet("faucet-msg");
  if (!w) return;
  try {
    const r = await post("/api/faucet/usd", {
      address: w.address,
      amount_cents: Math.round(parseFloat($("faucet-amount").value) * 100),
    });
    setMsg("faucet-msg", "balance: " + fmtUsd(r.usd_credits_cents), true);
  } catch (e) { setMsg("faucet-msg", e.message); }
}

async function depositAit() {
  const w = requireWallet("dep-msg");
  if (!w) return;
  try {
    const { address } = await api("/api/exchange/deposit-address");
    const r = await post("/api/wallet/sign-and-send", {
      private_key: w.private_key,
      recipient: address,
      amount: Math.round(parseFloat($("dep-amount").value) * COIN),
      fee: 0,
      memo: "DEPOSIT",
    });
    setMsg("dep-msg", "deposit pending: " + short(r.txid) + " (credits after next block)", true);
  } catch (e) { setMsg("dep-msg", e.message); }
}

async function withdrawAit() {
  const w = requireWallet("wd-msg");
  if (!w) return;
  try {
    const r = await post("/api/exchange/withdraw", {
      address: w.address,
      amount: Math.round(parseFloat($("wd-amount").value) * COIN),
      timestamp: Date.now() / 1000,
      private_key: w.private_key,
    });
    setMsg("wd-msg", "withdrawal pending: " + short(r.txid), true);
  } catch (e) { setMsg("wd-msg", e.message); }
}

async function placeOrder() {
  const w = requireWallet("order-msg");
  if (!w) return;
  try {
    const o = await post("/api/exchange/orders", {
      address: w.address,
      side: $("o-side").value,
      price_cents: Math.round(parseFloat($("o-price").value) * 100),
      quantity: Math.round(parseFloat($("o-qty").value) * COIN),
      timestamp: Date.now() / 1000,
      private_key: w.private_key,
    });
    setMsg("order-msg", `order ${o.status}, remaining ${fmtAit(o.remaining)} AIT`, true);
    refresh();
  } catch (e) { setMsg("order-msg", e.message); }
}

async function cancelOrder(id) {
  const w = wallet();
  try {
    await post(`/api/exchange/orders/${id}`, { address: w.address, private_key: w.private_key }, "DELETE");
    refresh();
  } catch (e) { setMsg("order-msg", e.message); }
}

function renderTrade(book, trades, myOrders) {
  const rows = Math.max(book.bids.length, book.asks.length, 1);
  let html = "";
  for (let i = 0; i < rows; i++) {
    const b = book.bids[i], a = book.asks[i];
    html += `<tr>
      <td class="buy">${b ? fmtAit(b.quantity) : ""}</td>
      <td class="buy">${b ? fmtUsd(b.price) : ""}</td>
      <td class="sell">${a ? fmtUsd(a.price) : ""}</td>
      <td class="sell">${a ? fmtAit(a.quantity) : ""}</td>
    </tr>`;
  }
  $("orderbook").querySelector("tbody").innerHTML = html;

  $("trades-table").querySelector("tbody").innerHTML = trades.map((t) => `
    <tr><td>${ago(t.timestamp)}</td><td>${fmtUsd(t.price)}</td>
    <td>${fmtAit(t.quantity)}</td><td class="fee">${fmtUsd(t.fee)}</td></tr>`).join("");

  if (trades.length) {
    $("t-last").textContent = fmtUsd(trades[0].price);
    const pts = trades.slice(0, 30).reverse().map((t) => t.price);
    const min = Math.min(...pts), max = Math.max(...pts), span = max - min || 1;
    const poly = pts.map((p, i) =>
      `${(i / Math.max(pts.length - 1, 1)) * 215 + 2},${38 - ((p - min) / span) * 34}`).join(" ");
    $("t-spark").innerHTML = `<polyline points="${poly}"></polyline>`;
  }

  $("open-orders").querySelector("tbody").innerHTML = (myOrders || []).map((o) => `
    <tr><td class="${o.side}">${o.side}</td><td>${fmtUsd(o.price)}</td>
    <td>${fmtAit(o.remaining)}</td>
    <td><button class="small danger" onclick="cancelOrder('${o.id}')">cancel</button></td></tr>`).join("");
}

/* --------------------------------------------------------- ai spend */
let providersCache = {};
function renderProviders(p) {
  providersCache = p;
  $("providers").innerHTML = Object.values(p).map((pr) => `
    <div class="card"><h3>${pr.name}</h3>
      <div class="big">${fmtAit(pr.price_per_1k_tokens)} AIT</div>
      <div class="sub">per 1k model tokens · total spent: ${fmtAit(pr.total_ait_spent)} AIT</div>
    </div>`).join("");
  const sel = $("ai-provider");
  if (sel.options.length !== Object.keys(p).length) {
    sel.innerHTML = Object.values(p).map((pr) => `<option value="${pr.id}">${pr.name}</option>`).join("");
  }
}

async function aiSpend() {
  const w = requireWallet("ai-msg");
  if (!w) return;
  try {
    const r = await post("/api/ai/spend", {
      provider: $("ai-provider").value,
      model_tokens: parseInt($("ai-tokens").value, 10),
      private_key: w.private_key,
    });
    setMsg("ai-msg", `spent ${fmtAit(r.cost)} AIT on ${r.provider} (${r.status})`, true);
    const pre = $("ai-receipt");
    pre.classList.remove("hidden");
    pre.textContent = JSON.stringify(r, null, 2);
  } catch (e) { setMsg("ai-msg", e.message); }
}

/* ---------------------------------------------------------- refresh */
async function refresh() {
  try {
    const st = await api("/api/status");
    $("st-height").textContent = st.height;
    $("st-diff").textContent = st.difficulty_bits;
    $("st-reward").textContent = fmtAit(st.block_reward);
    $("st-hashrate").textContent = fmtHashrate(st.estimated_hashrate);

    const tab = activeTab();
    if (tab === "explorer") renderBlocks(await api("/api/chain?limit=25"));

    if (tab === "mining") {
      $("mine-reward").textContent = fmtAit(st.block_reward) + " AIT";
      $("mine-halving").textContent = st.next_halving_height;
      $("mine-diff").textContent = st.difficulty_bits + " bits";
      $("mine-hashrate").textContent = fmtHashrate(st.estimated_hashrate) + "H/s";
      $("mine-mempool").textContent = st.mempool_size;
      const w = wallet();
      $("mine-cmd").textContent =
        `python -m aitoken miner --node ${location.origin} --address ${w ? w.address : "<your-address>"}`;
      const stats = await api("/api/mining/stats");
      $("miners-table").querySelector("tbody").innerHTML =
        Object.entries(stats.blocks_by_miner).map(([a, n]) =>
          `<tr><td class="addr">${a}</td><td>${n}</td></tr>`).join("");
    }

    if (tab === "wallet") {
      const w = wallet();
      $("wallet-none").classList.toggle("hidden", !!w);
      $("wallet-info").classList.toggle("hidden", !w);
      if (w) {
        const info = await api(`/api/address/${w.address}`);
        $("w-address").textContent = w.address;
        $("w-balance").textContent = fmtAit(info.balance) + " AIT";
        $("w-usd").textContent = fmtUsd(info.usd_credits_cents);
        $("w-usd-locked").textContent = fmtUsd(info.usd_locked_cents);
        $("w-exait").textContent = fmtAit(info.exchange_ait_available) + " AIT";
        $("w-exait-locked").textContent = fmtAit(info.exchange_ait_locked) + " AIT";
        $("wallet-txs").querySelector("tbody").innerHTML = info.recent_transactions.map((t) => `
          <tr><td class="hash">${short(t.txid)}</td><td>${t.height}</td>
          <td class="addr">${short(t.sender)}</td><td class="addr">${short(t.recipient)}</td>
          <td>${fmtAit(t.amount)} AIT</td></tr>`).join("");
      }
    }

    if (tab === "trade") {
      const w = wallet();
      const [book, trades, mine] = await Promise.all([
        api("/api/exchange/orderbook"),
        api("/api/exchange/trades?limit=30"),
        w ? api(`/api/exchange/orders?address=${w.address}`) : Promise.resolve({ orders: [] }),
      ]);
      renderTrade(book, trades.trades, mine.orders);
    }

    if (tab === "ai") renderProviders(await api("/api/ai/providers"));

    if (tab === "fees") {
      const f = await api("/api/exchange/fees");
      $("f-total").textContent = fmtUsd(f.total_fees_collected_cents);
      $("f-pct").textContent = f.fee_percent;
      $("f-balance").textContent = fmtUsd(f.owner_usd_balance_cents);
      $("f-trades").textContent = f.trade_count;
      $("f-owner").textContent = f.owner_address;
      $("fees-table").querySelector("tbody").innerHTML = f.recent_fee_events.map((t) => `
        <tr><td>${ago(t.timestamp)}</td><td class="hash">${short(t.id)}</td>
        <td>${fmtUsd(Math.floor(t.price * t.quantity / COIN))}</td>
        <td class="fee">${fmtUsd(t.fee)}</td><td class="addr">${short(t.taker)}</td></tr>`).join("");
    }
  } catch (e) {
    console.error(e);
  }
}

refresh();
setInterval(refresh, 2500);
