/** API types – mirror of backend/appro/api/schemas.py */
export type Severity = "critical" | "warning" | "info";

export interface ArticleRef {
  article_id: string; designation: string; unit: string; family: string; planner: string;
  coverage_target_days: number; alert_red_days: number; alert_yellow_days: number; overstock_days: number;
  safety_stock_qty: number; order_cycle_days: number; active: boolean;
}
export interface LinkRef { article_id: string; supplier_id: string; supplier_name: string; moq: number; pack_qty: number; lead_time_days: number; quota_pct: number; priority: number; active: boolean; }
export interface ProgramRef { program_id: string; name: string; family: string; active: boolean; components: number; }
export interface PdpErpLine { program_id: string; week_start: string | null; qty: number; version: string; }

/** Reference tables managed in the application (generic CRUD). */
export type RefValue = string | number | boolean | null;
export interface RefColumn { name: string; label: string; type: "str" | "float" | "int" | "bool" | "date"; key: boolean; required: boolean; description: string; }
export interface RefTableInfo { name: string; label: string; description: string; key: string[]; columns: RefColumn[]; rows: number; }
export type RefRow = Record<string, RefValue> & { updated_by?: string; updated_at?: string };

export interface AlertOut {
  article_id: string; designation: string; alert_type: string; severity: Severity; scope: string; message: string;
  date: string | null; value: number | null; details: Record<string, unknown>;
}
export interface ProposalOut {
  proposal_id: string; article_id: string; designation: string; unit: string; supplier_id: string | null; supplier_name: string;
  delivery_date: string; order_date: string; qty: number; net_requirement: number; reason: string; urgent: boolean;
  lead_time_days: number; moq: number; pack_qty: number; projected_stock_before: number; projected_stock_after: number;
}
export interface OrderInfo { order_id: string; supplier_id: string | null; order_type: string; expected_date: string; qty_ordered: number; qty_open: number; ref: string; ignored: boolean; }
export interface SeriesOut { key: string; label: string; values: number[]; }
/** One supplier of the article: its own Ferme / Prévisionnel / Reçu / Plan rows. */
/** One despatch advice (DESADV) line of the supplier ; shown in the Reçu row while not received. */
export interface DesadvInfo { desadv_id: string; packing_slip: string; issue_date: string; qty: number; state: string; final_processing: string; processed: boolean; received: boolean; hidden: boolean; purch_id: string; }
export interface ReceiptInfo { receipt_id: string; receipt_date: string; qty: number; packing_slip: string; purch_id: string; processed: boolean; }
export interface LaneOut { supplier_id: string | null; name: string; series: SeriesOut[]; plan_typed: boolean[]; orders_ignored: boolean[]; desadv_ko: boolean[]; receipts_ko: boolean[]; backlog_ordered: number; backlog_received: number; backlog_qty: number; orders: OrderInfo[]; desadv: DesadvInfo[]; receipt_lines: ReceiptInfo[]; }

export interface ArticleKpis {
  stock_on_hand: number; reference_correction: number; stock_reference: number; init_date: string; shortage_policy: "backlog" | "lost";
  stock_as_of_erp: number; stock_as_of_plan: number; coverage_erp_days: number; coverage_plan_days: number; coverage_target_days: number; target_stock: number;
  first_stockout_erp: string | null; first_stockout_plan: string | null; min_stock_erp: number; min_stock_plan: number; max_shortage_erp: number; max_shortage_plan: number;
  demand_next_7d: number; demand_next_30d: number; demand_horizon: number; avg_daily_demand_30d: number;
  open_firm_qty: number; open_forecast_qty: number; plan_qty: number; plan_cell_count: number; ignored_order_days: number; refused_proposals: number;
  backlog_qty: number; backlog_ordered: number; backlog_received: number;
  proposed_qty: number; proposal_count: number; urgent_proposal_count: number; alert_count: number; severity: Severity | null; actual_share_30d: number; lanes: number;
}
export interface ArticleSummary {
  article_id: string; designation: string; unit: string; planner: string; suppliers: string[]; severity: Severity | null;
  kpis: ArticleKpis; alert_types: string[]; sparkline: number[];
}
export interface BacklogRow { article_id: string; designation: string; unit: string; supplier_id: string | null; supplier_name: string; ordered: number; received: number; backlog: number; }
export interface CockpitKpis {
  articles: number; critical: number; warning: number; stockouts: number; stockouts_7d: number; low_coverage: number; overstock: number;
  backlog_articles: number; backlog_qty: number; proposals: number; urgent_proposals: number; proposals_qty: number; plan_articles: number; plan_qty: number;
  open_firm_qty: number; open_forecast_qty: number; avg_coverage_days: number | null; demand_next_30d: number;
}
export interface WeeklyOutlook { week: string; week_start: string; stockout_articles: number; below_target_articles: number; proposals: number; proposed_qty: number; }
export interface CockpitResponse {
  as_of: string; init_date: string; horizon_days: number; planner: string | null; data_source: string;
  pdp_version: { id: string; name: string } | null; kpis: CockpitKpis; articles: ArticleSummary[]; alerts: AlertOut[];
  proposals: ProposalOut[]; backlog: BacklogRow[]; diagnostics: string[]; weekly_supply_demand: WeeklyOutlook[];
}
export type Granularity = "default" | "day" | "week";
export interface ProjectionResponse {
  article: ArticleRef; as_of: string; init_date: string; granularity: Granularity; periods: string[]; period_start: string[]; period_end: string[]; series: SeriesOut[];
  lanes: LaneOut[]; proposals: ProposalOut[]; alerts: AlertOut[]; kpis: ArticleKpis; suppliers: LinkRef[];
  programs: { program_id: string; name: string; qty_per: number; unit: string; production_next_30d: number }[]; diagnostics: string[];
}
export interface GridArticle { article: ArticleRef; series: SeriesOut[]; lanes: LaneOut[]; kpis: ArticleKpis; suppliers: LinkRef[]; programs: string[]; }
/** One page of the supply table (``total`` articles after filters). */
export interface GridResponse { as_of: string; init_date: string; granularity: Granularity; periods: string[]; period_start: string[]; period_end: string[]; articles: GridArticle[]; diagnostics: string[]; total: number; page: number; page_size: number; }
/** The Plan row as ERP delivery schedule lines. */
export interface DeliveryRow { supplier_id: string | null; supplier_name: string; week: string; date: string; end_date: string; qty: number; typed_qty: number; erp_qty: number; cbn_qty: number; typed: boolean; }
export interface DeliveryPlanResponse { article_id: string; designation: string; unit: string; as_of: string; suppliers: { supplier_id: string | null; name: string }[]; rows: DeliveryRow[]; }
export type ImpactLayer = "onhand" | "erp" | "plan";
export interface ProgramImpact {
  program_id: string; name: string; components: number; planned: number[];
  feasible: Record<ImpactLayer, number[]>; limiting: Record<ImpactLayer, { article_id: string; share: number }[][]>; first_impact: Record<ImpactLayer, string | null>;
}
export interface ProgramImpactResponse { as_of: string; weeks: string[]; programs: ProgramImpact[]; diagnostics: string[]; }
export interface WeeklyParamRow { week: string; week_start: string; values: Record<string, number>; overridden: string[]; }
export interface WeeklyParamsResponse { article_id: string; fields: string[]; defaults: Record<string, number>; weeks: WeeklyParamRow[]; }

/** The two editable rows. */
export interface AdjustmentOut { id: string; article_id: string; date: string; expression: string; qty: number; note: string; updated_by: string; updated_at: string; }
export interface PlanCellOut { id: string; article_id: string; supplier_id: string; date: string; expression: string; qty: number; note: string; updated_by: string; updated_at: string; }
/** A click on a read-only cell: ignored firm-order day (supplier × day), refused CBN proposal (supplier × day, blocks its week) or hidden unreceived DESADV (supplier × day). */
export type FlagKind = "order_ignored" | "proposal_refused" | "desadv_hidden";
export interface FlagOut { id: string; article_id: string; supplier_id: string; date: string; kind: FlagKind; qty: number; note: string; updated_by: string; updated_at: string; }
export interface FlagIn { article_id: string; supplier_id?: string | null; date: string; kind: FlagKind; qty?: number; note?: string; }

export interface ParamDoc { field: string; default: unknown; type: string; description: string; options: string[] | null; }
export interface ParamOverrideOut { id: string; scope: string; key1: string; key2: string; field: string; value: string; updated_by: string; updated_at: string; }
export interface PdpVersionOut { id: string; name: string; source_file: string; note: string; active: boolean; imported_by: string; imported_at: string; line_count: number; programs: number; first_week: string | null; last_week: string | null; }
export interface ImportReport { created: number; ignored: number; notes: string[]; version: PdpVersionOut | null; }
export interface AuditOut { id: number; ts: string; user: string; action: string; entity_type: string; entity_id: string; article_id: string | null; payload: Record<string, unknown>; }
/** Rights of the signed-in user (backend/appro/services/access.py). */
export type Role = "reader" | "appro" | "manager" | "admin";
export interface AccessOut {
  user: string; role: Role; planner_id: string | null; name: string | null; portfolio: string[]; delegated_from: string[]; bootstrap: boolean;
  can_write: boolean; can_edit_all: boolean; can_manage_params: boolean; can_import_pdp: boolean; is_admin: boolean;
}
/** ``as_of`` = today (the real date, or the simulated one) ; ``init_date`` = stock initialisation day, point zero of the application. */
export interface ConfigOut { title: string; data_source: Record<string, unknown>; as_of: string; init_date: string; horizon_days: number; planners: string[]; default_planner: string | null; user: string; version: string; reference_empty: boolean; access: AccessOut; }

export interface PerimeterItem { id: string; name: string; }
export interface PerimeterOut { planner: string | null; articles: number; programs: PerimeterItem[]; suppliers: PerimeterItem[]; }
export interface PdpSheetWeek { week: string; week_start: string; editable: boolean; }
export interface PdpSheetProgram { program_id: string; name: string; active: boolean; source: "app" | "erp" | "none"; values: number[]; }
export interface PdpSheetOut { as_of: string; current_week: string; active_version: PdpVersionOut | null; erp_available: boolean; weeks: PdpSheetWeek[]; programs: PdpSheetProgram[]; }
