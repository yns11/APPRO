import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Params } from "./api";
import type {
  ActionOut, AdherenceResponse, AdjustmentOut, ArticleRef, AuditOut, BacklogRow, CockpitResponse, ConsumptionSourceOption, DeliveryPlanResponse, FixedRule, FlagOut, GridResponse, LinkRef, OrphanRow, ParamDoc, ParamOverrideOut,
  FlowsResponse, PdpSheetOut, PdpVersionOut, PerimeterOut, PlanCellOut, PlantCalendar, SearchResponse, ProgramImpactResponse, ProgramRef, ProjectionResponse, RefRow, RefTableInfo, SupplierDetail, SupplierRef, SyncStatus, WeeklyParamsResponse,
} from "./types";
import { usePerimeter } from "@/state/PerimeterContext";
import { useToast } from "@/components/ui";

/** Every write invalidates the engine-derived queries (the backend cache is bumped too). */
export function useInvalidateAll() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries();
}

export function useCockpit(extra?: Params) {
  const { engineParams, perimeter } = usePerimeter();
  const params = { ...engineParams, ...extra };
  return useQuery({ queryKey: ["cockpit", params], queryFn: () => api.get<CockpitResponse>("/api/cockpit", params), enabled: perimeter.planner !== undefined, staleTime: 30_000 });
}

export function useProjection(articleId: string | undefined, extra?: Params) {
  const { perimeter, tableParams } = usePerimeter();
  const params = { granularity: perimeter.granularity, ...tableParams, ...extra };
  return useQuery({
    queryKey: ["projection", articleId, params],
    queryFn: () => api.get<ProjectionResponse>(`/api/articles/${encodeURIComponent(articleId!)}/projection`, params),
    enabled: !!articleId, staleTime: 30_000,
  });
}

export function useFlows() {
  const { engineParams } = usePerimeter();
  return useQuery({ queryKey: ["flows", engineParams.planner], queryFn: () => api.get<FlowsResponse>("/api/flows", { planner: engineParams.planner }), staleTime: 30_000, placeholderData: keepPreviousData });
}
export const useSearch = (params: Params, enabled = true) => useQuery({ queryKey: ["search", params], queryFn: () => api.get<SearchResponse>("/api/search", params), enabled, placeholderData: keepPreviousData });

export function useBacklog() {
  const { engineParams } = usePerimeter();
  const params = { planner: engineParams.planner };
  return useQuery({ queryKey: ["backlog", params], queryFn: () => api.get<BacklogRow[]>("/api/backlog", params), staleTime: 30_000 });
}

/** One page of the multi-article supply table (same columns for every article) ; the previous page
 * stays on screen while the next one loads. */
export function useGrid(extra?: Params) {
  const { engineParams, perimeter, tableParams } = usePerimeter();
  const params = { ...engineParams, granularity: perimeter.granularity, ...tableParams, ...extra };
  return useQuery({ queryKey: ["grid", params], queryFn: () => api.get<GridResponse>("/api/grid", params), staleTime: 30_000, placeholderData: keepPreviousData });
}

export function useDeliveryPlan(articleId: string | undefined) {
  return useQuery({ queryKey: ["delivery-plan", articleId], enabled: !!articleId, staleTime: 30_000,
    queryFn: () => api.get<DeliveryPlanResponse>(`/api/articles/${encodeURIComponent(articleId!)}/delivery-plan`) });
}

export function useProgramImpact() {
  const { engineParams } = usePerimeter();
  return useQuery({ queryKey: ["program-impact", engineParams], queryFn: () => api.get<ProgramImpactResponse>("/api/programs/impact", engineParams), staleTime: 30_000 });
}

/** The two editable rows: typed plan cells and adjustment cells. */
export const usePlanCells = (params?: Params) => useQuery({ queryKey: ["plan-cells", params], queryFn: () => api.get<PlanCellOut[]>("/api/entries/plan", params) });
export const useAdjustments = (params?: Params) => useQuery({ queryKey: ["adjustments", params], queryFn: () => api.get<AdjustmentOut[]>("/api/entries/adjustments", params) });
/** Clicks on read-only cells: ignored firm-order days, refused CBN proposals. */
export const useFlags = (params?: Params) => useQuery({ queryKey: ["flags", params], queryFn: () => api.get<FlagOut[]>("/api/entries/flags", params) });
/** Weekly stock-policy calendar of an article: from the reference week to the last PDP week / end of horizon. */
export const useWeeklyParams = (articleId: string | null) => useQuery({
  queryKey: ["weekly-params", articleId], enabled: !!articleId,
  queryFn: () => api.get<WeeklyParamsResponse>(`/api/articles/${encodeURIComponent(articleId!)}/weekly-params`),
});

export const useArticles = (planner?: string | null) => useQuery({ queryKey: ["ref-articles", planner], queryFn: () => api.get<ArticleRef[]>("/api/reference/articles", { planner }), staleTime: 300_000 });
export const useLinks = (articleId?: string, supplierId?: string) => useQuery({ queryKey: ["ref-links", articleId ?? null, supplierId ?? null], queryFn: () => api.get<LinkRef[]>("/api/reference/links", { article_id: articleId, supplier_id: supplierId }), staleTime: 300_000 });
export const useSuppliers = () => useQuery({ queryKey: ["ref-suppliers"], queryFn: () => api.get<SupplierRef[]>("/api/reference/suppliers"), staleTime: 300_000 });
export const useSupplier = (supplierId: string | undefined) => useQuery({ queryKey: ["supplier", supplierId], enabled: !!supplierId, staleTime: 30_000, queryFn: () => api.get<SupplierDetail>(`/api/suppliers/${encodeURIComponent(supplierId!)}`) });
export const usePrograms = () => useQuery({ queryKey: ["ref-programs"], queryFn: () => api.get<ProgramRef[]>("/api/reference/programs"), staleTime: 300_000 });
export const useRefTables = () => useQuery({ queryKey: ["ref-tables"], queryFn: () => api.get<RefTableInfo[]>("/api/reference/tables") });
export const useRefRows = (name: string | null, planner?: string | null) => useQuery({ queryKey: ["ref-rows", name, planner ?? null], queryFn: () => api.get<RefRow[]>(`/api/reference/${name}/rows`, { planner: planner ?? undefined }), enabled: !!name });
/** Articles eligible as consumption sources of an article (derived consumption). */
export const useConsumptionSources = (articleId: string | undefined, enabled = true) => useQuery({ queryKey: ["consumption-sources", articleId], enabled: !!articleId && enabled, queryFn: () => api.get<ConsumptionSourceOption[]>("/api/reference/consumption-sources", { article_id: articleId }) });

export const useParamSchema = () => useQuery({ queryKey: ["param-schema"], queryFn: () => api.get<ParamDoc[]>("/api/params/schema"), staleTime: Infinity });
export const useFixedRules = () => useQuery({ queryKey: ["param-fixed"], queryFn: () => api.get<FixedRule[]>("/api/params/fixed"), staleTime: Infinity });
export const useParamEffective = () => useQuery({ queryKey: ["param-effective"], queryFn: () => api.get<Record<string, unknown>>("/api/params/effective") });
export const useOverrides = (params?: Params) => useQuery({ queryKey: ["overrides", params], queryFn: () => api.get<ParamOverrideOut[]>("/api/params/overrides", params) });
export const usePerimeterLists = (planner: string | null | undefined) => useQuery({ queryKey: ["perimeter", planner ?? null], queryFn: () => api.get<PerimeterOut>("/api/reference/perimeter", { planner: planner ?? undefined }), staleTime: 300_000 });
export const usePdpSheet = (weeks?: number, planner?: string | null) => useQuery({ queryKey: ["pdp-sheet", weeks, planner ?? null], queryFn: () => api.get<PdpSheetOut>("/api/pdp/sheet", { weeks, planner: planner ?? undefined }), placeholderData: keepPreviousData });
export const usePdpVersions = () => useQuery({ queryKey: ["pdp-versions"], queryFn: () => api.get<PdpVersionOut[]>("/api/pdp/versions") });
export const useAudit = (params?: Params) => useQuery({ queryKey: ["audit", params], queryFn: () => api.get<AuditOut[]>("/api/entries/audit", params) });
/** The undo journal (7 days) ; ``mine`` = the caller's own actions. */
export const useActions = (params?: Params) => useQuery({ queryKey: ["actions", params], queryFn: () => api.get<ActionOut[]>("/api/actions", params), staleTime: 10_000 });

/** Paramètres › Données */
export const useSyncStatus = (refetchMs = 120_000) => useQuery({ queryKey: ["data-sync"], queryFn: () => api.get<SyncStatus>("/api/data/sync"), staleTime: 60_000, refetchInterval: refetchMs });
export const useOrphans = () => useQuery({ queryKey: ["data-orphans"], queryFn: () => api.get<OrphanRow[]>("/api/data/orphans"), staleTime: 60_000 });
export const usePlantCalendar = () => useQuery({ queryKey: ["data-calendar"], queryFn: () => api.get<PlantCalendar>("/api/data/calendar"), staleTime: 300_000 });
export const useAdherence = (params: Params, enabled = true) => useQuery({ queryKey: ["data-adherence", params], queryFn: () => api.get<AdherenceResponse>("/api/data/adherence", params), enabled, staleTime: 60_000, placeholderData: keepPreviousData });

/** Generic mutation that invalidates everything on success and shows the server message on failure
 * (a refused write – 403 outside the user's portfolio, 422 invalid value – is never silent). */
export function useWrite<TIn, TOut = unknown>(fn: (input: TIn) => Promise<TOut>, onDone?: (out: TOut) => void) {
  const invalidate = useInvalidateAll();
  const toast = useToast();
  return useMutation({ mutationFn: fn, onSuccess: (out) => { invalidate(); onDone?.(out); }, onError: (e) => toast.push((e as Error).message, "error") });
}
