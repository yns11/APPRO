/** API types – mirror of backend/appro/api/schemas.py */
export type Severity = "critical" | "warning" | "info";

export interface ArticleRef {
  article_id: string; designation: string; unit: string; family: string; planner: string;
  coverage_target_days: number; alert_red_days: number; alert_yellow_days: number; overstock_days: number;
  safety_stock_qty: number; lot_policy: string; order_cycle_days: number; active: boolean;
}
export interface SupplierRef { supplier_id: string; name: string; country: string; contact: string; delivery_weekdays: number[]; active: boolean; }
export interface LinkRef { article_id: string; supplier_id: string; supplier_name: string; moq: number; pack_qty: number; lead_time_days: number; quota_pct: number; priority: number; active: boolean; }
export interface ProgramRef { program_id: string; name: string; family: string; active: boolean; components: number; }
export interface BomRef { program_id: string; program_name: string; article_id: string; qty_per: number; unit: string; scrap_pct: number; }
export interface PlanLine { program_id: string; week_start: string | null; iso_week: string; qty: number; version: string; }

export interface AlertOut {
  article_id: string; designation: string; alert_type: string; severity: Severity; scope: string; message: string;
  date: string | null; value: number | null; details: Record<string, unknown>;
}
export interface ProposalOut {
  proposal_id: string; article_id: string; designation: string; unit: string; supplier_id: string | null; supplier_name: string;
  delivery_date: string; order_date: string; qty: number; net_requirement: number; reason: string; urgent: boolean; ignored: boolean;
  lead_time_days: number; moq: number; pack_qty: number; projected_stock_before: number; projected_stock_after: number;
}
export interface SupplyEventOut { date: string; kind: string; ref: string; qty: number; supplier_id: string | null; order_type: string; source: string; late: boolean; }

export interface ArticleKpis {
  stock_on_hand: number; snapshot_date: string; shortage_policy: "backlog" | "lost";
  stock_as_of_firm: number; stock_as_of_forecast: number; stock_as_of_sim: number;
  coverage_firm_days: number; coverage_forecast_days: number; coverage_sim_days: number; coverage_target_days: number; target_stock: number;
  first_stockout_firm: string | null; first_stockout_forecast: string | null; first_stockout_sim: string | null;
  min_stock_firm: number; min_stock_forecast: number; min_stock_sim: number;
  max_shortage_firm: number; max_shortage_forecast: number; max_shortage_sim: number;
  demand_next_7d: number; demand_next_30d: number; demand_horizon: number; avg_daily_demand_30d: number;
  open_firm_qty: number; open_forecast_qty: number; open_planned_qty: number; proposed_qty: number; proposal_count: number; urgent_proposal_count: number;
  late_order_count: number; late_order_qty: number; alert_count: number; severity: Severity | null; actual_share_30d: number;
}
export interface ArticleSummary {
  article_id: string; designation: string; unit: string; planner: string; suppliers: string[]; severity: Severity | null;
  kpis: ArticleKpis; alert_types: string[]; sparkline: number[];
}
export interface CockpitKpis {
  articles: number; critical: number; warning: number; stockouts: number; stockouts_7d: number; low_coverage: number; overstock: number;
  late_orders: number; proposals: number; urgent_proposals: number; proposals_qty: number; open_firm_qty: number; open_forecast_qty: number; open_planned_qty: number;
  avg_coverage_days: number | null; demand_next_30d: number;
}
export interface WeeklyOutlook { week: string; week_start: string; stockout_articles: number; below_target_articles: number; proposals: number; proposed_qty: number; }
export interface CockpitResponse {
  as_of: string; horizon_days: number; planner: string | null; scenario_id: string | null; data_source: string;
  pdp_version: { id: string; name: string } | null; kpis: CockpitKpis; articles: ArticleSummary[]; alerts: AlertOut[];
  proposals: ProposalOut[]; diagnostics: string[]; weekly_supply_demand: WeeklyOutlook[];
}
export interface SeriesOut { key: string; label: string; values: number[]; }
export interface ProjectionResponse {
  article: ArticleRef; as_of: string; granularity: "day" | "week"; periods: string[]; period_start: string[]; series: SeriesOut[];
  events: SupplyEventOut[]; proposals: ProposalOut[]; alerts: AlertOut[]; kpis: ArticleKpis; suppliers: LinkRef[];
  programs: { program_id: string; name: string; qty_per: number; unit: string; production_next_30d: number }[]; diagnostics: string[];
}

export interface OrderOut { id: string; article_id: string; supplier_id: string | null; expected_date: string; qty: number; unit: string; order_type: string; status: string; source: string; erp_order_id: string | null; proposal_id: string | null; note: string; created_by: string; created_at: string; updated_at: string; }
export interface ReceiptOut { id: string; article_id: string; supplier_id: string | null; order_id: string | null; receipt_date: string; qty: number; note: string; created_by: string; created_at: string; }
export interface AdjustmentOut { id: string; article_id: string; date: string; qty: number; movement_type: string; comment: string; created_by: string; created_at: string; }
export interface ProductionOut { id: string; program_id: string; date: string; qty: number; created_by: string; created_at: string; }

export interface ScenarioEvent { id?: string; seq?: number; kind: string; payload: Record<string, unknown>; label: string; }
export interface ScenarioOut { id: string; name: string; description: string; status: string; params: Record<string, unknown>; events: ScenarioEvent[]; created_by: string; created_at: string; updated_at: string; }
export interface CompareArticle { article_id: string; designation: string; unit: string; base: Record<string, unknown>; scenario: Record<string, unknown>; delta_min_stock: number; delta_max_shortage: number; delta_coverage: number; stockout_changed: boolean; }
export interface CompareResponse { as_of: string; base_kpis: CockpitKpis; scenario_kpis: CockpitKpis; articles: CompareArticle[]; diagnostics: string[]; }

export interface ParamDoc { field: string; default: unknown; type: string; description: string; options: string[] | null; }
export interface ParamOverrideOut { id: string; scope: string; key1: string; key2: string; field: string; value: string; updated_by: string; updated_at: string; }
export interface PdpVersionOut { id: string; name: string; source_file: string; note: string; active: boolean; imported_by: string; imported_at: string; line_count: number; programs: number; first_week: string | null; last_week: string | null; }
export interface ImportReport { created: number; ignored: number; notes: string[]; version: PdpVersionOut | null; }
export interface AuditOut { id: number; ts: string; user: string; action: string; entity_type: string; entity_id: string; article_id: string | null; payload: Record<string, unknown>; }
export interface ConfigOut { title: string; data_source: Record<string, unknown>; as_of: string; horizon_days: number; planners: string[]; default_planner: string | null; user: string; version: string; }
