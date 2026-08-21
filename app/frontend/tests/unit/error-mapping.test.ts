/**
 * Tests for the closed failure → display-text mapping (F32): every
 * error the UI can receive is classified into a fixed set of kinds
 * and displayed through a single place — the server's own message
 * for http failures, a fixed internationalized phrase otherwise.
 */
import { describe, expect, it } from "vitest";
import i18n from "i18next";
import { ApiError, ContractError, normalizeApiError } from "@/services/bimemo-api";
import { errorToDisplayText, QUERY_KEYS } from "@/hooks/use-server-state";

const t = i18n.t;

describe("normalizeApiError", () => {
  it("classifies ApiError as an http failure carrying its status", () => {
    expect(normalizeApiError(new ApiError(500, "boom"))).toEqual({
      kind: "http",
      status: 500,
      message: "boom",
    });
  });

  it("classifies ContractError as a contract failure before the ApiError check", () => {
    expect(normalizeApiError(new ContractError("bad shape"))).toEqual({
      kind: "contract",
      status: 0,
      message: "bad shape",
    });
  });

  it("classifies anything without an HTTP response as a network failure", () => {
    expect(normalizeApiError(new Error("net down"))).toEqual({
      kind: "network",
      status: null,
      message: "net down",
    });
    expect(normalizeApiError("plain string")).toEqual({
      kind: "network",
      status: null,
      message: "plain string",
    });
  });
});

describe("errorToDisplayText", () => {
  it("shows the server's own message for http failures", () => {
    expect(errorToDisplayText(new ApiError(500, "boom"), t)).toBe("boom");
  });

  it("falls back to the generic http phrase when the server sent no message", () => {
    expect(errorToDisplayText(new ApiError(503, ""), t)).toBe(t("errors.http"));
  });

  it("shows a fixed phrase for contract failures", () => {
    expect(errorToDisplayText(new ContractError("shape"), t)).toBe(
      t("errors.contract")
    );
  });

  it("shows a fixed phrase for network failures", () => {
    expect(errorToDisplayText(new Error("offline"), t)).toBe(t("errors.network"));
  });
});

describe("QUERY_KEYS", () => {
  it("gives one stable cache entry per server resource", () => {
    // A filtered list is a distinct resource per filter value, so two
    // different filters never share a cache entry.
    expect(QUERY_KEYS.memory("")).toEqual(["memory", ""]);
    expect(QUERY_KEYS.memory("docker")).toEqual(["memory", "docker"]);
    expect(QUERY_KEYS.memory("docker")).toEqual(QUERY_KEYS.memory("docker"));
    expect(QUERY_KEYS.memoryEntity("e1")).toEqual(["memory-entity", "e1"]);
    expect(QUERY_KEYS.memoryEntity("e1")).not.toEqual(QUERY_KEYS.memoryEntity("e2"));
    // Collection and detail are distinct cache entries: the plural
    // list key never matches the singular detail key, so pages must
    // name both explicitly when a change affects both.
    expect(QUERY_KEYS.skills).toEqual(["skills"]);
    expect(QUERY_KEYS.skill("foo")).toEqual(["skill", "foo"]);
    expect(QUERY_KEYS.llmConfig).toEqual(["llm-config"]);
    expect(QUERY_KEYS.agentTokens).toEqual(["agent-tokens"]);
    expect(QUERY_KEYS.caRoot).toEqual(["ca-root"]);
  });
});
