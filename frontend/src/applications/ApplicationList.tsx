import type {
  Application,
  ApplicationStatus,
  RoleType,
  Season,
} from "../api/types";
import {
  ALL,
  UNKNOWN_VALUE,
  countByStatus,
  filterApplications,
  sortApplications,
  yearOptions,
  type Filters,
  type SortKey,
} from "./filtering";
import {
  ROLE_TYPE_LABELS,
  SEASON_LABELS,
  STATUS_LABELS,
  UNKNOWN,
  formatCycle,
  formatTimestamp,
} from "./labels";

const SORT_LABELS: Record<SortKey, string> = {
  updated: "Last update",
  application_date: "Application date",
  company: "Company",
};

interface ApplicationListProps {
  applications: Application[];
  filters: Filters;
  onFiltersChange: (filters: Filters) => void;
  sortKey: SortKey;
  onSortChange: (sortKey: SortKey) => void;
  onSelect: (applicationId: string) => void;
  onCreate: () => void;
}

export function ApplicationList({
  applications,
  filters,
  onFiltersChange,
  sortKey,
  onSortChange,
  onSelect,
  onCreate,
}: ApplicationListProps) {
  const visible = sortApplications(filterApplications(applications, filters), sortKey);
  const counts = countByStatus(visible);

  function setFilter<K extends keyof Filters>(field: K, value: Filters[K]) {
    onFiltersChange({ ...filters, [field]: value });
  }

  return (
    <section className="page" aria-labelledby="applications-title">
      <div className="page-header">
        <h1 id="applications-title">Applications</h1>
        <button type="button" className="primary" onClick={onCreate}>
          New application
        </button>
      </div>

      <dl className="summary" aria-label="Status summary">
        <div>
          <dt>Showing</dt>
          <dd>
            {visible.length} of {applications.length}
          </dd>
        </div>
        {(Object.keys(STATUS_LABELS) as ApplicationStatus[]).map((status) => (
          <div key={status}>
            <dt>{STATUS_LABELS[status]}</dt>
            <dd>{counts[status]}</dd>
          </div>
        ))}
      </dl>

      <div className="filters">
        <div className="field">
          <label htmlFor="filter-role-type">Role type</label>
          <select
            id="filter-role-type"
            value={filters.roleType}
            onChange={(event) =>
              setFilter("roleType", event.target.value as Filters["roleType"])
            }
          >
            <option value={ALL}>All role types</option>
            {(Object.keys(ROLE_TYPE_LABELS) as RoleType[]).map((roleType) => (
              <option key={roleType} value={roleType}>
                {ROLE_TYPE_LABELS[roleType]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="filter-season">Season</label>
          <select
            id="filter-season"
            value={filters.season}
            onChange={(event) =>
              setFilter("season", event.target.value as Filters["season"])
            }
          >
            <option value={ALL}>All seasons</option>
            {(Object.keys(SEASON_LABELS) as Season[]).map((season) => (
              <option key={season} value={season}>
                {SEASON_LABELS[season]}
              </option>
            ))}
            <option value={UNKNOWN_VALUE}>{UNKNOWN}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="filter-year">Year</label>
          <select
            id="filter-year"
            value={filters.year}
            onChange={(event) => setFilter("year", event.target.value)}
          >
            <option value={ALL}>All years</option>
            {yearOptions(applications).map((year) => (
              <option key={year} value={String(year)}>
                {year}
              </option>
            ))}
            <option value={UNKNOWN_VALUE}>{UNKNOWN}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="filter-status">Status</label>
          <select
            id="filter-status"
            value={filters.status}
            onChange={(event) =>
              setFilter("status", event.target.value as Filters["status"])
            }
          >
            <option value={ALL}>All statuses</option>
            {(Object.keys(STATUS_LABELS) as ApplicationStatus[]).map((status) => (
              <option key={status} value={status}>
                {STATUS_LABELS[status]}
              </option>
            ))}
            <option value={UNKNOWN_VALUE}>{UNKNOWN}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="filter-search">Search</label>
          <input
            id="filter-search"
            type="search"
            placeholder="Company or role"
            value={filters.search}
            onChange={(event) => setFilter("search", event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="sort-by">Sort by</label>
          <select
            id="sort-by"
            value={sortKey}
            onChange={(event) => onSortChange(event.target.value as SortKey)}
          >
            {(Object.keys(SORT_LABELS) as SortKey[]).map((key) => (
              <option key={key} value={key}>
                {SORT_LABELS[key]}
              </option>
            ))}
          </select>
        </div>
      </div>

      {visible.length === 0 ? (
        <p className="empty">
          {applications.length === 0
            ? "No applications yet."
            : "No applications match these filters."}
        </p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Company</th>
                <th scope="col">Role</th>
                <th scope="col">Role type</th>
                <th scope="col">Cycle</th>
                <th scope="col">Status</th>
                <th scope="col">Applied</th>
                <th scope="col">Last update</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((application) => (
                <tr key={application.application_id}>
                  <td>
                    <button
                      type="button"
                      className="link"
                      onClick={() => onSelect(application.application_id)}
                    >
                      {application.company ?? UNKNOWN}
                    </button>
                  </td>
                  <td>{application.role ?? UNKNOWN}</td>
                  <td>{ROLE_TYPE_LABELS[application.role_type ?? "unknown"]}</td>
                  <td>{formatCycle(application.season, application.year)}</td>
                  <td>
                    {application.status === null
                      ? UNKNOWN
                      : STATUS_LABELS[application.status]}
                  </td>
                  <td>{application.application_date ?? UNKNOWN}</td>
                  <td>{formatTimestamp(application.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
