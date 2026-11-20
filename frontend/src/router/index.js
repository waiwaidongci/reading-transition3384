export const routes=['overview','source','config','results','splits','notes','audit'];
export function route(){const hash=location.hash.slice(1)||'overview';return routes.includes(hash)?hash:'overview'}
