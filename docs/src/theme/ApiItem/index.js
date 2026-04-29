import ApiItem from '@theme-original/ApiItem';

export default function ApiItemWrapper(props) {
  const Content = props.content;
  const frontMatter = Content.frontMatter?.api
    ? Content.frontMatter
    : Content.metadata?.frontMatter ?? Content.frontMatter;

  if (!frontMatter?.api) {
    return <ApiItem {...props} />;
  }

  function ContentWithFrontMatter(contentProps) {
    return <Content {...contentProps} />;
  }

  Object.assign(ContentWithFrontMatter, Content, { frontMatter });

  return <ApiItem {...props} content={ContentWithFrontMatter} />;
}
