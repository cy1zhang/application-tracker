import type { Application } from "../api/types";
import { UNKNOWN, formatTimestamp } from "../applications/labels";
import { EditableCell, type EditableField } from "./EditableCell";
import type { SortKey } from "./sorting";

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "company", label: "Company" },
  { key: "role", label: "Role" },
  { key: "role_type", label: "Type" },
  { key: "cycle", label: "Cycle" },
  { key: "status", label: "Status" },
  { key: "application_date", label: "Applied" },
  { key: "updated_at", label: "Last update" },
];

type Props = {
  applications: Application[];
  sortKey: SortKey;
  direction: "asc" | "desc";
  onSort: (key: SortKey) => void;
  onSelect: (applicationId: string) => void;
  onReload: () => Promise<void> | void;
};

export function SheetGrid({ applications, sortKey, direction, onSort, onSelect, onReload }: Props) {
  return (
    <div className="sheet-grid-scroll" role="region" aria-label="Applications grid" tabIndex={0}>
      <table className="sheet-grid">
        <thead><tr>{COLUMNS.map(({ key, label }) => (
          <th key={key} scope="col" aria-sort={sortKey === key ? (direction === "asc" ? "ascending" : "descending") : undefined}>
            <button type="button" onClick={() => onSort(key)}>{label}</button>
          </th>
        ))}</tr></thead>
        <tbody>{applications.map((application) => (
          <tr key={application.application_id}>
            <td aria-label={application.company ?? UNKNOWN}>
              <EditableCell application={application} field="company" onSaved={onReload} onConflict={onReload} />
              <button type="button" className="sheet-detail-button" aria-label={application.company ?? UNKNOWN} title="Open details" onClick={() => onSelect(application.application_id)} />
            </td>
            {(["role", "role_type", "cycle", "status", "application_date"] as EditableField[]).map((field) => (
              <td key={field}><EditableCell application={application} field={field} onSaved={onReload} onConflict={onReload} /></td>
            ))}
            <td>{formatTimestamp(application.updated_at)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}
