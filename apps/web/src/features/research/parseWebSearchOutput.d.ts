export type ResearchResult = {
    title: string;
    url: string;
    snippet: string;
};
export declare const parseWebSearchOutput: (output: string) => ResearchResult[];
