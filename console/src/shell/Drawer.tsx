import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { ItemDetail } from '../components/ItemDetail';

interface DrawerCtx {
  openId: string | null;
  open: (id: string) => void;
  close: () => void;
}
const Ctx = createContext<DrawerCtx>({ openId: null, open: () => {}, close: () => {} });

export function DrawerProvider({ children }: { children: ReactNode }) {
  const loc = useLocation();
  const [openId, setOpenId] = useState<string | null>(() => new URLSearchParams(window.location.search).get('item'));
  const open = useCallback((id: string) => setOpenId(id), []);
  const close = useCallback(() => setOpenId(null), []);
  useEffect(() => {
    const id = new URLSearchParams(loc.search).get('item');
    setOpenId(id);
  }, [loc.pathname, loc.search]);
  return (
    <Ctx.Provider value={{ openId, open, close }}>
      {children}
      <Drawer id={openId} onClose={close} />
    </Ctx.Provider>
  );
}

export function useDrawer() {
  return useContext(Ctx);
}

function Drawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const [shown, setShown] = useState<string | null>(id);
  useEffect(() => {
    if (id) setShown(id);
  }, [id]);
  const visible = !!id;
  return (
    <div className={`drawer-root${visible ? ' is-open' : ''}`} aria-hidden={!visible}>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Work item" onTransitionEnd={() => !visible && setShown(null)}>
        {shown && <ItemDetail key={shown} id={shown} inDrawer onClose={onClose} />}
      </aside>
    </div>
  );
}
