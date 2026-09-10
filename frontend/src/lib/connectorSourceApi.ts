import { api } from "@/lib/api";
import type { SourceAcquisitionAttempt, SourceSnapshot, ConnectorSourceSelection as ConnectorSource, ConnectorSourceScope as ConnectorScope } from "@/types/semanticWorkspace";

export type { ConnectorSourceSelection as ConnectorSource, ConnectorSourceScope as ConnectorScope } from "@/types/semanticWorkspace";
export type ConnectorRequest = { source: ConnectorSource; purpose: string; expected_connection_version: string; resume_checkpoint: boolean };
export type ConnectorAttempt = Omit<SourceAcquisitionAttempt, "allowed_scope" | "snapshot"> & { allowed_scope: ConnectorScope; snapshot: SourceSnapshot<ConnectorScope> | null };
export type OwnerConnection = { connection_id: string; name: string; dialect: string };
export type ConnectionSchema = { default_schema: string | null; tables: Array<{ name: string; schema: string | null; primary_key: string[]; columns: Array<{ name: string; type: string }> }> };
export function listOwnerConnections(): Promise<OwnerConnection[]> { return api.get("/api/data-sources/connections"); }
export function getConnectionSchema(id: string): Promise<ConnectionSchema> { return api.get(`/api/data-sources/connections/${encodeURIComponent(id)}/schema`); }
export function resolveConnectorSource(source: ConnectorSource): Promise<ConnectorScope> { return api.post("/api/connector-sources/resolve", source); }
export function acquireConnectorSource(payload: ConnectorRequest, key: string): Promise<ConnectorAttempt> { return api.post("/api/connector-sources/acquisitions", payload, { "Idempotency-Key": key }); }
export function getConnectorAcquisition(id: string): Promise<ConnectorAttempt> { return api.get(`/api/semantic-workspace/source-acquisitions/${encodeURIComponent(id)}`); }
export function cancelConnectorAcquisition(id: string): Promise<ConnectorAttempt> { return api.post(`/api/semantic-workspace/source-acquisitions/${encodeURIComponent(id)}/cancel`); }
