/**
 * Single source of the human-readable reasons behind the quality
 * score. The backend report carries raw counts only; every view that
 * explains the score derives its sentences here (audit: the score was
 * shown with no reasons nearby, and two views synthesized diverging
 * texts).
 */
import i18n from "@/i18n";
import type {
  QualityData,
  RouteDiagnosticsSummaryData,
} from "@/components/admin/memory-debug/types";

export function buildQualityIssues(quality: QualityData): string[] {
  const issues: string[] = [];
  if (quality.assertions_with_missing_evidence > 0) {
    issues.push(
      i18n.t("memoryDebug.issueMissingEvidence", {
        count: quality.assertions_with_missing_evidence,
      })
    );
  }
  if (quality.assertions_without_evidence > 0) {
    issues.push(
      i18n.t("memoryDebug.issueNoEvidence", {
        count: quality.assertions_without_evidence,
      })
    );
  }
  if (quality.duplicate_assertion_texts > 0) {
    issues.push(
      i18n.t("memoryDebug.issueDuplicateTexts", {
        count: quality.duplicate_assertion_texts,
      })
    );
  }
  if (quality.orphan_entities > 0) {
    issues.push(
      i18n.t("memoryDebug.issueOrphanEntities", {
        count: quality.orphan_entities,
      })
    );
  }
  return issues;
}

export function buildAttentionItems(
  quality: QualityData | null,
  diagnostics: RouteDiagnosticsSummaryData | null
): string[] {
  const items: string[] = quality ? buildQualityIssues(quality) : [];

  if (diagnostics) {
    if (diagnostics.totals.mismatch_rows > 0) {
      items.push(
        i18n.t("memoryDebug.issueRouteMismatches", {
          count: diagnostics.totals.mismatch_rows,
        })
      );
    }
    if (diagnostics.totals.fallback_rows > 0) {
      items.push(
        i18n.t("memoryDebug.issueRouteFallbacks", {
          count: diagnostics.totals.fallback_rows,
        })
      );
    }
  }

  return items.slice(0, 5);
}
