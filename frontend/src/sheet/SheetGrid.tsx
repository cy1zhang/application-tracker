import type { Application } from "../api/types";
import { ROLE_TYPE_LABELS, STATUS_LABELS, UNKNOWN, formatCycle, formatTimestamp } from "../applications/labels";
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
};

export function SheetGrid({ applications, sortKey, direction, onSort, onSelect }: Props) {
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
            <td><button type="button" className="link" onClick={() => onSelect(application.application_id)}>{application.company ?? UNKNOWN}</button></td>
            <td>{application.role ?? UNKNOWN}</td>
            <td>{ROLE_TYPE_LABELS[application.role_type ?? "unknown"]}</td>
            <td>{formatCycle(application.season, application.year)}</td>
            <td>{application.status === null ? UNKNOWN : STATUS_LABELS[application.status]}</td>
            <td>{application.application_date ?? UNKNOWN}</td>
            <td>{formatTimestamp(application.updated_at)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}
