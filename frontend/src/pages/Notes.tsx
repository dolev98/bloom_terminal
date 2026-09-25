import { Page } from '../ui'
import NotesWorkspace from '../components/notes/NotesWorkspace'

export default function NotesPage() {
  return (
    <Page title="Notes" sub="Your research notes. Tag a note with tickers and it also appears on those company pages; the price at the time you wrote it is kept.">
      <NotesWorkspace />
    </Page>
  )
}
