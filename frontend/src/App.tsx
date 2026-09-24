import "./styles.css";

function App() {
  return (
    <div className="app-shell">
      <header className="site-header">
        <span className="wordmark-mark" aria-hidden="true">
          AT
        </span>
        <span className="wordmark-name">Application Tracker</span>
      </header>

      <main className="foundation-layout">
        <section className="milestone" aria-labelledby="page-title">
          <p className="status-line">
            <span className="status-dot" aria-hidden="true" />
            Project foundation
          </p>
          <h1 id="page-title">Application Tracker</h1>
          <p className="milestone-lead">
            Manual tracking is the first milestone.
          </p>
          <p className="milestone-detail">
            Gmail synchronization and AI classification come after the core
            application record and its history are reliable.
          </p>
        </section>

        <aside className="scope-note" aria-labelledby="scope-title">
          <h2 id="scope-title">Current scope</h2>
          <dl>
            <div>
              <dt>Now</dt>
              <dd>Development foundation</dd>
            </div>
            <div>
              <dt>Next</dt>
              <dd>Owner-scoped manual records</dd>
            </div>
          </dl>
        </aside>
      </main>
    </div>
  );
}

export default App;
