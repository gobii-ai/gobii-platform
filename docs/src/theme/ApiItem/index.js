import DocItem from '@theme/DocItem';

let ApiItem;

function getApiItem() {
  if (!ApiItem) {
    ApiItem = require('@theme-original/ApiItem').default;
  }
  return ApiItem;
}

export default function ApiItemWrapper(props) {
  const Content = props.content;
  const frontMatter = Content.frontMatter?.api
    ? Content.frontMatter
    : Content.metadata?.frontMatter ?? Content.frontMatter;

  if (!frontMatter?.api) {
    return <DocItem {...props} />;
  }

  const OpenApiItem = getApiItem();

  function ContentWithFrontMatter(contentProps) {
    return <Content {...contentProps} />;
  }

  Object.assign(ContentWithFrontMatter, Content, { frontMatter });

  return <OpenApiItem {...props} content={ContentWithFrontMatter} />;
}
