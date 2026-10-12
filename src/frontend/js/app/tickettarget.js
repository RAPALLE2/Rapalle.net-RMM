// tickettarget.js
// ---------------
// Ein Ticket kann sich auf einen Client beziehen - oder auf das RMM selbst
// ("Haupt-RMM"). Für den zweiten Fall gibt es keinen echten Client, deshalb
// diese feste Kennung. Sie wird wie eine Client-ID gespeichert; das Backend
// legt sie unverändert in ticket_clients ab und braucht dafür keine Sonderfälle.

import { state } from "./state.js";

export const RMM_TARGET = "__rmm__";
export const RMM_LABEL = "Haupt-RMM (Server)";

export function isRmmTarget(id) {
  return id === RMM_TARGET;
}

// Anzeigename eines Ticket-Ziels - egal ob Client oder das RMM selbst.
export function targetLabel(id) {
  if (isRmmTarget(id)) return `🏢 ${RMM_LABEL}`;
  const c = state.clients.find((x) => x.id === id);
  return c ? `🖥️ ${c.hostname}` : `🖥️ ${String(id).slice(0, 8)}…`;
}
