import NotesWorkspace from '../../components/notes/NotesWorkspace'

/** Company tab: notes tagged with this ticker; new notes are tagged with it automatically. */
export default function Notes({ ticker }: { ticker: string }) {
  return <NotesWorkspace key={ticker} ticker={ticker} />
}
