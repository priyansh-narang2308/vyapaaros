"use client";

import { useMetrics } from "@/hooks/useMetrics";
import { MetricsHeader } from "./MetricsHeader";
import { KPIPanel } from "./panels/KPIPanel";
import { RevenuePanel } from "./panels/RevenuePanel";
import { AgentPerformancePanel } from "./panels/AgentPerformancePanel";
import { RecommendationAttributionPanel } from "./panels/RecommendationAttributionPanel";
import { PromotionPanel } from "./panels/PromotionPanel";
import { ProductHealthPanel } from "./panels/ProductHealthPanel";

export function MetricsDashboard() {
  const { state, setTimeRange, refresh } = useMetrics();
  const {
    timeRange,
    isLoading,
    lastUpdated,
    kpis,
    revenueData,
    agentPerformance,
    recommendationAttribution,
    promotionBreakdown,
    productHealth,
  } = state;

  return (
    <div className="dashboard-grid">
      {}
      <MetricsHeader
        timeRange={timeRange}
        onTimeRangeChange={setTimeRange}
        onRefresh={refresh}
        isLoading={isLoading}
        lastUpdated={lastUpdated}
      />

      {}
      <KPIPanel kpis={kpis} isLoading={isLoading} />

      {}
      <RevenuePanel data={revenueData} timeRange={timeRange} isLoading={isLoading} />

      {}
      <div className="dashboard-row two-col">
        <AgentPerformancePanel data={agentPerformance} isLoading={isLoading} />
        <PromotionPanel data={promotionBreakdown} isLoading={isLoading} />
      </div>

      <RecommendationAttributionPanel data={recommendationAttribution} isLoading={isLoading} />

      {}
      <ProductHealthPanel data={productHealth} isLoading={isLoading} />
    </div>
  );
}
