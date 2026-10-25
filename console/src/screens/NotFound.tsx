import { Link } from 'react-router-dom';

export function NotFound() {
  return (
    <div className="page">
      <div className="panel empty-panel">
        <p className="empty-title">There is no page at this address.</p>
        <p className="ink-2">
          <Link className="link" to="/">
            Go to Today
          </Link>
        </p>
      </div>
    </div>
  );
}
