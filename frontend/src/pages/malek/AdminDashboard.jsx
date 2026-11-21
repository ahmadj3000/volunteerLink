export default function AdminDashboard() {
  return (
    <section className="panel">
      <span className="eyebrow">ADMINISTRATION</span>
      <h2>NGO approvals</h2>
      <p>Review organizations requesting access to publish opportunities.</p>
      <div className="empty-state">
        <h3>Approval dashboard awaiting connection</h3>
        <p>Pending organizations will come from the NGO backend.</p>
      </div>
    </section>
  )
}
