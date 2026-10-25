import { useParams } from 'react-router-dom';
import { ItemDetail } from '../components/ItemDetail';

export function ItemPage() {
  const { id = '' } = useParams();
  return (
    <div className="page page-item">
      <ItemDetail key={id} id={id} />
    </div>
  );
}
