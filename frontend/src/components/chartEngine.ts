// ECharts, tree-shaken to the pieces the charts use. Loaded by Chart on mount, so a page's text
// paints before this (the largest library on the dashboards) has downloaded.
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, CanvasRenderer]);

export { echarts };
