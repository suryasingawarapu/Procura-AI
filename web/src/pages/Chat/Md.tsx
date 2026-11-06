import clsx from "clsx";
import { Fragment } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const blockComponents: Components = {
  a: ({ node: _n, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
  table: ({ node: _n, ...props }) => (
    <div className="md__table-wrap">
      <table {...props} />
    </div>
  ),
};

// Inline mode: the paragraph wrapper is dropped so the text can sit inside a sentence.
const inlineComponents: Components = { ...blockComponents, p: ({ children }) => <Fragment>{children}</Fragment> };

/** Safe markdown (no raw HTML is ever interpreted): bold, italic, code, lists, tables. */
export function Md({ text, streaming = false, inline = false }: { text: string; streaming?: boolean; inline?: boolean }) {
  return (
    <div className={clsx("md", streaming && "md--streaming", inline && "md--inline")}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={inline ? inlineComponents : blockComponents}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
