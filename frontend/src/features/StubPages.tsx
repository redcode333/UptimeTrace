import { useLocation } from "react-router-dom";
import { PageHeader } from "../components/PageHeader";
import { flatNav } from "../components/shell/nav";
import { NotAvailable } from "../components/states/States";

/** Pages that exist in the approved navigation but have no backend: they say so and what they need. */
export function UnavailablePage() {
  const { pathname } = useLocation();
  const item = flatNav().find((i) => i.path === pathname);
  return (
    <>
      <PageHeader title={item?.label ?? "Not found"} />
      <NotAvailable reason={item?.unavailable ?? "This page does not exist."} />
    </>
  );
}

