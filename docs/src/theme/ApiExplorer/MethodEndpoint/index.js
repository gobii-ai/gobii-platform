function colorForMethod(method) {
  switch (method.toLowerCase()) {
    case 'get':
      return 'primary';
    case 'post':
      return 'success';
    case 'delete':
      return 'danger';
    case 'put':
      return 'info';
    case 'patch':
      return 'warning';
    default:
      return 'secondary';
  }
}

export default function MethodEndpoint({ method, path }) {
  return (
    <>
      <pre className="openapi__method-endpoint">
        <span className={`badge badge--${colorForMethod(method)}`}>
          {method === 'event' ? 'Webhook' : method.toUpperCase()}
        </span>{' '}
        {method !== 'event' && (
          <h2 className="openapi__method-endpoint-path">
            {path.replace(/{([a-z0-9-_]+)}/gi, ':$1').replace(/\/$/, '') || '/'}
          </h2>
        )}
      </pre>
      <div className="openapi__divider" />
    </>
  );
}
