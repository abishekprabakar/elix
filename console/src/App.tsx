import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { StoreProvider } from './lib/store';
import { Activity } from './screens/Activity';
import { Agents } from './screens/Agents';
import { Audit } from './screens/Audit';
import { Inbox } from './screens/Inbox';
import { ItemPage } from './screens/ItemPage';
import { Ledger } from './screens/Ledger';
import { NotFound } from './screens/NotFound';
import { Policies } from './screens/Policies';
import { Proof } from './screens/Proof';
import { Reconcile } from './screens/Reconcile';
import { Scenarios } from './screens/Scenarios';
import { Today } from './screens/Today';
import { Layout } from './shell/Layout';

export function App() {
  return (
    <BrowserRouter>
      <StoreProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Today />} />
            <Route path="inbox" element={<Inbox />} />
            <Route path="activity" element={<Activity />} />
            <Route path="items/:id" element={<ItemPage />} />
            <Route path="reconcile" element={<Reconcile />} />
            <Route path="ledger" element={<Ledger />} />
            <Route path="policies" element={<Policies />} />
            <Route path="agents" element={<Agents />} />
            <Route path="audit" element={<Audit />} />
            <Route path="proof" element={<Proof />} />
            <Route path="scenarios" element={<Scenarios />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </StoreProvider>
    </BrowserRouter>
  );
}
